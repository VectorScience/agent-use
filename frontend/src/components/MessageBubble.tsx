// Render one message bubble. Handles user / assistant / system, text blocks
// (markdown) and tool_use blocks (collapsed card).
//
// Memoised: a chat can have hundreds of turns and React re-renders the whole
// list when new state arrives; memoising per-message keeps it smooth.

import { memo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Message } from "../types";
import { IconBolt, IconUser } from "./Icons";

interface Props {
  message: Message;
}

export const MessageBubble = memo(function MessageBubble({ message }: Props) {
  const isUser = message.role === "user";
  const isSystem = message.role === "system";

  if (isSystem) {
    return (
      <div className="flex justify-center my-2">
        <span className="text-[11px] text-text-faint px-2 py-0.5 rounded-full bg-bg-elevated">
          {message.blocks.map((b) => b.text).join(" ")}
        </span>
      </div>
    );
  }

  return (
    <div className={`flex gap-2.5 ${isUser ? "flex-row-reverse" : "flex-row"} my-1.5`}>
      <Avatar isUser={isUser} />
      <div className={`flex flex-col gap-1 min-w-0 max-w-[88%] ${isUser ? "items-end" : "items-start"}`}>
        {message.blocks.map((block, i) => (
          <BlockRenderer
            key={i}
            block={block}
            isUser={isUser}
          />
        ))}
        {message.error && (
          <div className="text-[11px] text-err px-2 py-1 rounded-md bg-err/10 border border-err/30">
            错误：{message.error}
          </div>
        )}
      </div>
    </div>
  );
});

function Avatar({ isUser }: { isUser: boolean }) {
  return (
    <div
      className={`flex-shrink-0 w-6 h-6 rounded-md flex items-center justify-center ${
        isUser ? "bg-accent/15 text-accent" : "bg-bg-elevated text-text-muted"
      }`}
    >
      {isUser ? <IconUser width={14} height={14} /> : <IconBolt width={12} height={12} />}
    </div>
  );
}

function BlockRenderer({
  block,
  isUser,
}: {
  block: Message["blocks"][number];
  isUser: boolean;
}) {
  if (block.type === "text" && block.text) {
    const text = stripCursorWrappers(block.text);
    return (
      <div
        className={`rounded-2xl px-3 py-2 break-words ${
          isUser
            ? "bg-accent text-white rounded-tr-sm"
            : "bg-bg-elevated text-text rounded-tl-sm"
        }`}
      >
        {isUser ? (
          <p className="text-[14px] leading-relaxed whitespace-pre-wrap">{text}</p>
        ) : (
          <div className="md-body">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
          </div>
        )}
      </div>
    );
  }

  if (block.type === "tool_use") {
    return <ToolUseCard name={block.tool_name} input={block.tool_input} />;
  }

  if (block.type === "tool_result") {
    if (!block.text.trim()) return null;
    return (
      <div className="text-[11.5px] font-mono text-text-muted bg-black/30 rounded-md px-2 py-1.5 border border-line max-h-32 overflow-auto scrollbar-thin whitespace-pre-wrap break-words">
        {block.text.slice(0, 1200)}
        {block.text.length > 1200 ? "\n…" : ""}
      </div>
    );
  }

  return null;
}

function ToolUseCard({
  name,
  input,
}: {
  name: string;
  input: Record<string, unknown>;
}) {
  const [open, setOpen] = useState(false);
  const summary = formatToolSummary(name, input);

  return (
    <button
      onClick={() => setOpen((v) => !v)}
      className="w-full text-left rounded-lg border border-line bg-bg-elevated/60 hover:bg-bg-elevated transition-colors px-2.5 py-1.5"
    >
      <div className="flex items-center gap-2">
        <span className="text-accent">
          <IconBolt width={12} height={12} />
        </span>
        <span className="text-[12px] font-mono font-medium text-accent">{name}</span>
        <span className="text-[11.5px] text-text-muted truncate flex-1">{summary}</span>
        <span className="text-[10px] text-text-faint">{open ? "−" : "+"}</span>
      </div>
      {open && (
        <pre className="mt-1.5 text-[11px] font-mono text-text-muted overflow-x-auto scrollbar-thin">
          {JSON.stringify(input, null, 2)}
        </pre>
      )}
    </button>
  );
}

function formatToolSummary(name: string, input: Record<string, unknown>): string {
  // Pick the most informative single field per known tool. Falls back to a
  // generic "first string field" heuristic.
  const fields: Record<string, string> = {
    Read: "path",
    Write: "path",
    Edit: "path",
    StrReplace: "path",
    Shell: "command",
    Grep: "pattern",
    Glob: "pattern",
  };
  const key = fields[name];
  if (key && typeof input[key] === "string") {
    return truncate(String(input[key]), 80);
  }
  // Generic: first string-valued field.
  for (const v of Object.values(input)) {
    if (typeof v === "string" && v.length > 0) return truncate(v, 80);
  }
  return "";
}

function truncate(s: string, n: number): string {
  const oneLine = s.replace(/\s+/g, " ").trim();
  return oneLine.length > n ? oneLine.slice(0, n) + "…" : oneLine;
}

// Cursor wraps user text in <timestamp>…</timestamp>\n<user_query>…</user_query>
// tags. Strip them for a cleaner mobile view.
function stripCursorWrappers(text: string): string {
  if (!text.includes("<user_query>")) return text;
  const m = text.match(/<user_query>\s*([\s\S]*?)\s*<\/user_query>/);
  if (m) return m[1].trim();
  return text;
}
