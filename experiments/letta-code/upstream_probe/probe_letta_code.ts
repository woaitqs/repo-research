/**
 * Runtime probe of letta-code's real LocalBackend (no network, no model).
 *
 * It drives the actual `LocalBackend` class with a capturing executor, so every
 * provider call's inputs (system prompt, mid-conversation prompt, message view)
 * are recorded exactly as the backend assembled them.
 *
 * Run from the root of a letta-code clone (the `@/` alias and bunfig loaders
 * resolve from there): see ../run.sh `probe`.
 */
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { Stream } from "@letta-ai/letta-client/core/streaming";
import type { LettaStreamingResponse } from "@letta-ai/letta-client/resources/agents/messages";
import type { ConversationMessageCreateBody } from "@/backend";
import {
  contextCompactionThreshold,
  shouldCompactForContextPressure,
} from "@/backend/dev/provider-turn-executor";
import type {
  HeadlessTurnExecutor,
  HeadlessTurnExecutorInput,
} from "@/backend/dev/headless-turn-executor";
import { LocalBackend } from "@/backend/local/local-backend";

type Chunk = LettaStreamingResponse;

function streamOf(chunks: Chunk[]): Stream<Chunk> {
  return {
    controller: new AbortController(),
    async *[Symbol.asyncIterator]() {
      for (const chunk of chunks) yield chunk;
    },
  } as unknown as Stream<Chunk>;
}

async function drain(stream: Stream<Chunk>): Promise<Chunk[]> {
  const out: Chunk[] = [];
  for await (const chunk of stream) out.push(chunk);
  return out;
}

/** Scripted executor: answers with the next scripted reply and records inputs. */
class CapturingExecutor implements HeadlessTurnExecutor {
  calls: HeadlessTurnExecutorInput[] = [];
  private toolSeq = 0;
  next: "text" | "tool" = "text";

  async execute(input: HeadlessTurnExecutorInput) {
    this.calls.push(input);
    if (this.next === "tool") {
      this.toolSeq += 1;
      return streamOf([
        {
          message_type: "approval_request_message",
          tool_call: {
            tool_call_id: `call-${this.toolSeq}`,
            name: "Bash",
            arguments: JSON.stringify({ command: "echo hi" }),
          },
        } as Chunk,
        { message_type: "stop_reason", stop_reason: "requires_approval" } as Chunk,
      ]);
    }
    return streamOf([
      {
        message_type: "assistant_message",
        content: [{ type: "text", text: "ok" }],
      } as Chunk,
      { message_type: "stop_reason", stop_reason: "end_turn" } as Chunk,
    ]);
  }
}

function userTurn(agentId: string, text: string): ConversationMessageCreateBody {
  return {
    agent_id: agentId,
    messages: [{ role: "user", content: text }],
  } as ConversationMessageCreateBody;
}

function approvalTurn(
  agentId: string,
  toolCallId: string,
  toolReturn: string,
): ConversationMessageCreateBody {
  return {
    agent_id: agentId,
    messages: [
      {
        type: "approval",
        approvals: [
          { type: "tool", tool_call_id: toolCallId, tool_return: toolReturn, status: "success" },
        ],
      },
    ],
  } as unknown as ConversationMessageCreateBody;
}

/** Does this provider call's model-visible input contain `needle` anywhere? */
function visible(call: HeadlessTurnExecutorInput, needle: string) {
  return {
    inSystemPrompt: (call.systemPrompt ?? "").includes(needle),
    inMidConversationPrompt: (call.midConversationSystemPrompt ?? "").includes(needle),
    inMessages: JSON.stringify(call.uiMessages).includes(needle),
  };
}

function seen(v: ReturnType<typeof visible>): boolean {
  return v.inSystemPrompt || v.inMidConversationPrompt || v.inMessages;
}

const results: Array<Record<string, unknown>> = [];
function record(id: string, claim: string, observed: unknown, pass: boolean) {
  results.push({ id, claim, observed, pass });
}

async function main() {
  const storageDir = mkdtempSync(join(tmpdir(), "lc-probe-"));
  const executor = new CapturingExecutor();
  const summaryComplete = async () => ({
    role: "assistant",
    content: [{ type: "text", text: "SUMMARY_OF_EARLY_TURNS" }],
    api: "openai-completions",
    provider: "probe",
    model: "probe",
    usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
    stopReason: "stop",
    timestamp: Date.now(),
  });
  const backend = new LocalBackend({ storageDir, executor, complete: summaryComplete as never });
  const agent = await backend.createAgent({
    name: "Probe",
    system: "BASE PROMPT {CORE_MEMORY}",
  } as never);
  const conv = await backend.createConversation({ agent_id: agent.id } as never);
  const memoryDir = join(storageDir, "memfs", agent.id, "memory");

  // P1: one provider call per run; tool calls end the run with requires_approval;
  // the approval message becomes a toolResult in the next call's message view.
  executor.next = "tool";
  const run1 = await drain(
    await backend.createConversationMessageStream(conv.id, userTurn(agent.id, "list files")),
  );
  executor.next = "text";
  const run2 = await drain(
    await backend.createConversationMessageStream(
      conv.id,
      approvalTurn(agent.id, "call-1", "file-a\nfile-b"),
    ),
  );
  const stop1 = run1.filter((c) => c.message_type === "stop_reason").map((c) => (c as { stop_reason?: string }).stop_reason);
  const call2Roles = executor.calls[1]?.uiMessages.map((m) => m.role);
  record(
    "P1",
    "A tool call ends the run with requires_approval; the client's approval becomes a toolResult that the next provider call sees",
    { run1StopReasons: stop1, run2Chunks: run2.length, executorCalls: executor.calls.length, call2Roles },
    stop1.includes("requires_approval") &&
      executor.calls.length === 2 &&
      JSON.stringify(call2Roles) === JSON.stringify(["user", "assistant", "toolResult"]),
  );

  // P2: uncommitted memory edits are invisible (prompt renders from git HEAD).
  writeFileSync(
    join(memoryDir, "human.md"),
    '---\nname: "human"\ndescription: "About the user"\n---\nUNCOMMITTED_FACT\n',
  );
  const beforeP2 = executor.calls.length;
  await drain(await backend.createConversationMessageStream(conv.id, userTurn(agent.id, "hello")));
  const p2 = visible(executor.calls[beforeP2]!, "UNCOMMITTED_FACT");
  record("P2", "An uncommitted memory edit does not reach the model", p2, !seen(p2));

  // P3: a committed memory edit arrives once as a <memory_update> delta, the
  // system prompt itself stays byte-identical (cache-stable).
  writeFileSync(
    join(memoryDir, "human.md"),
    '---\nname: "human"\ndescription: "About the user"\n---\nCOMMITTED_FACT_TEAL\n',
  );
  execFileSync("git", ["add", "human.md"], { cwd: memoryDir });
  execFileSync("git", ["-c", "user.name=probe", "-c", "user.email=p@x", "commit", "-qm", "probe"], { cwd: memoryDir });
  const sysBefore = executor.calls.at(-1)!.systemPrompt;
  const firstAfterCommit = executor.calls.length;
  executor.next = "tool"; // turn A: user message -> tool call
  await drain(await backend.createConversationMessageStream(conv.id, userTurn(agent.id, "turn A")));
  executor.next = "text"; // same turn, next step: approval -> text
  await drain(
    await backend.createConversationMessageStream(conv.id, approvalTurn(agent.id, "call-2", "done")),
  );
  await drain(await backend.createConversationMessageStream(conv.id, userTurn(agent.id, "turn B")));
  const afterCommit = executor.calls.slice(firstAfterCommit).map((c) => visible(c, "COMMITTED_FACT_TEAL"));
  const sysAfter = executor.calls.slice(firstAfterCommit).map((c) => c.systemPrompt === sysBefore);
  record(
    "P3a",
    "The first provider call after a memory commit carries a <memory_update> delta; the system prompt is unchanged",
    { firstCall: afterCommit[0], systemPromptUnchanged: sysAfter },
    afterCommit[0]!.inMidConversationPrompt && sysAfter.every(Boolean),
  );
  record(
    "P3b",
    "(staleness check) Later provider calls in the same conversation still see the committed memory",
    { laterCalls: afterCommit.slice(1) },
    afterCommit.slice(1).every(seen),
  );

  // P4: compaction is append-only on disk and recompiles the system prompt.
  const convDir = readdirSync(join(storageDir, "conversations"))
    .map((d) => join(storageDir, "conversations", d))
    .find((d) => {
      try {
        return readFileSync(join(d, "messages.jsonl"), "utf8").length > 0;
      } catch {
        return false;
      }
    })!;
  const rowsBefore = readFileSync(join(convDir, "messages.jsonl"), "utf8").trim().split("\n");
  const viewBefore = backend["store"].listLocalMessages(conv.id, agent.id).length;
  const compact = await backend.compactConversationMessages(conv.id, {
    agent_id: agent.id,
    compaction_settings: { mode: "all" },
  } as never);
  const rowsAfter = readFileSync(join(convDir, "messages.jsonl"), "utf8").trim().split("\n");
  const types = rowsAfter.map((r) => (JSON.parse(r) as { type: string }).type);
  const viewAfter = backend["store"].listLocalMessages(conv.id, agent.id);
  record(
    "P4a",
    "Compaction appends a compaction entry; every earlier transcript row is kept on disk",
    {
      rowsBefore: rowsBefore.length,
      rowsAfter: rowsAfter.length,
      lastRowType: types.at(-1),
      priorRowsUnchanged: rowsBefore.every((r, i) => r === rowsAfter[i]),
      modelViewBefore: viewBefore,
      modelViewAfter: viewAfter.length,
      numMessagesAfter: compact.num_messages_after,
    },
    rowsAfter.length === rowsBefore.length + 1 &&
      types.at(-1) === "compaction" &&
      rowsBefore.every((r, i) => r === rowsAfter[i]) &&
      viewAfter.length < viewBefore,
  );
  await drain(await backend.createConversationMessageStream(conv.id, userTurn(agent.id, "after compaction")));
  const postCompact = executor.calls.at(-1)!;
  record(
    "P4b",
    "After compaction the next call sees the summary as a user-role system_alert, and a recompiled system prompt that now includes the committed memory",
    {
      firstMessageRole: postCompact.uiMessages[0]?.role,
      summaryVisible: JSON.stringify(postCompact.uiMessages[0]).includes("SUMMARY_OF_EARLY_TURNS"),
      systemAlert: JSON.stringify(postCompact.uiMessages[0]).includes("system_alert"),
      committedMemoryInSystemPrompt: (postCompact.systemPrompt ?? "").includes("COMMITTED_FACT_TEAL"),
    },
    postCompact.uiMessages[0]?.role === "user" &&
      JSON.stringify(postCompact.uiMessages[0]).includes("SUMMARY_OF_EARLY_TURNS") &&
      (postCompact.systemPrompt ?? "").includes("COMMITTED_FACT_TEAL"),
  );

  // P5: an interrupted tool call (no approval sent) is settled with a synthetic
  // error result before the next non-approval turn, so the provider never sees
  // a dangling tool_use.
  executor.next = "tool";
  await drain(await backend.createConversationMessageStream(conv.id, userTurn(agent.id, "start tool")));
  executor.next = "text";
  await drain(await backend.createConversationMessageStream(conv.id, userTurn(agent.id, "never mind")));
  const p5 = executor.calls.at(-1)!.uiMessages;
  const settled = p5.find(
    (m) => m.role === "toolResult" && (m as { isError?: boolean }).isError === true,
  );
  record(
    "P5",
    "A dangling tool call is answered with a synthetic error toolResult before the next user turn",
    { tailRoles: p5.slice(-4).map((m) => m.role), settledText: settled ? JSON.stringify((settled as { content: unknown }).content) : null },
    Boolean(settled),
  );

  // P6: one active run per conversation.
  executor.next = "tool";
  const pending = await backend.createConversationMessageStream(conv.id, userTurn(agent.id, "busy"));
  let concurrentError: string | null = null;
  const iterator = (pending as AsyncIterable<Chunk>)[Symbol.asyncIterator]();
  await iterator.next(); // start run, do not finish it
  try {
    await backend.createConversationMessageStream(conv.id, userTurn(agent.id, "second"));
  } catch (error) {
    concurrentError = (error as Error).message;
  }
  record(
    "P6",
    "A second turn while a run is active is rejected",
    { concurrentError },
    concurrentError !== null && concurrentError.includes("already has an active run"),
  );

  // P7: proactive compaction threshold (reserve 16,384 tokens, at most 20% of the window).
  const windows = [8_192, 32_768, 128_000, 200_000, 1_000_000];
  const thresholds = Object.fromEntries(windows.map((w) => [w, contextCompactionThreshold(w)]));
  record(
    "P7",
    "Preflight compaction fires above window - min(16384, 20% of window)",
    {
      thresholds,
      at200k_180k: shouldCompactForContextPressure({ contextTokens: 180_000, contextWindow: 200_000 }),
      at200k_185k: shouldCompactForContextPressure({ contextTokens: 185_000, contextWindow: 200_000 }),
    },
    thresholds[200_000] === 183_616 && thresholds[32_768] === 26_215,
  );

  console.log(JSON.stringify({ storageDir, results }, null, 2));
  const failed = results.filter((r) => !r.pass).map((r) => r.id);
  console.error(`probe: ${results.length - failed.length}/${results.length} claims held${failed.length ? `; did not hold: ${failed.join(", ")}` : ""}`);
}

await main();
