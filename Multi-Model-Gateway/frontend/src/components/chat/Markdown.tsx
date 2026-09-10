import { memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";

function MarkdownImpl({ content }: { content: string }) {
  return (
    <div className="gw-markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[[rehypeHighlight, { detect: true, ignoreMissing: true }]]}
        components={{
          a({ node, ...props }) {
            void node; // strip the AST node so it isn't spread onto the DOM
            return <a {...props} target="_blank" rel="noopener noreferrer" />;
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

export const Markdown = memo(MarkdownImpl);
