/**
 * MessageRenderer — Chat 消息渲染分发器
 *
 * 检测消息内容类型，路由到对应渲染器：
 *   - TokUI DSL → TokUIChatRenderer
 *   - 纯文本/Markdown → MarkdownRenderer（原有渲染）
 */

import React, { useMemo } from "react";
import { isTokUIDSL, TokUIChatRenderer } from "./TokUIChatRenderer";

interface MessageRendererProps {
  content: string;
  workspaceId: string;
  sseEndpoint?: string;
  theme?: string;
  onDSLRendered?: (dsl: string, componentType: string) => void;
  onDSLFailed?: (dsl: string, error: Error) => void;
  MarkdownRenderer?: React.ComponentType<{ content: string }>;
}

const DefaultMarkdownRenderer: React.FC<{ content: string }> = ({ content }) => (
  <div
    className="markdown-content"
    style={{ lineHeight: 1.6, fontSize: "14px" }}
    dangerouslySetInnerHTML={{ __html: content }}
  />
);

export const MessageRenderer: React.FC<MessageRendererProps> = ({
  content,
  workspaceId,
  sseEndpoint,
  theme,
  onDSLRendered,
  onDSLFailed,
  MarkdownRenderer = DefaultMarkdownRenderer,
}) => {
  const isDSL = useMemo(() => isTokUIDSL(content), [content]);

  if (isDSL) {
    return (
      <TokUIChatRenderer
        content={content}
        workspaceId={workspaceId}
        sseEndpoint={sseEndpoint}
        theme={theme}
        onDSLRendered={onDSLRendered}
        onDSLFailed={onDSLFailed}
      />
    );
  }

  return <MarkdownRenderer content={content} />;
};

export default MessageRenderer;