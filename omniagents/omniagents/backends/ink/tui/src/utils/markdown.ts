/**
 * Simple markdown rendering for terminal output
 * Matches key features of Bubbletea's Glamour markdown rendering
 * Uses ANSI escape codes to style text with Tokyo Night theme colors
 */

import chalk from 'chalk';

/**
 * Render markdown text to terminal-formatted string
 * This matches how Bubbletea uses Glamour to render assistant messages
 *
 * Supports:
 * - Code blocks (```)
 * - Inline code (`)
 * - Bold (**)
 * - Italic (*)
 * - Headers (#)
 * - Lists (-, *, numbered)
 * - Links [text](url)
 */
export function renderMarkdown(markdown: string): string {
  try {
    let output = markdown;

    // Process line by line to handle code blocks and lists
    const lines = output.split('\n');
    const processedLines: string[] = [];
    let inCodeBlock = false;
    let codeBlockContent: string[] = [];
    let codeBlockLanguage = '';

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];

      // Handle code blocks
      if (line.trim().startsWith('```')) {
        if (!inCodeBlock) {
          // Start of code block
          inCodeBlock = true;
          codeBlockLanguage = line.trim().slice(3).trim();
          codeBlockContent = [];
        } else {
          // End of code block
          inCodeBlock = false;
          // Render code block with purple color (matching Tokyo Night)
          if (codeBlockContent.length > 0) {
            processedLines.push(chalk.hex('#bb9af7')(codeBlockContent.join('\n')));
          }
          codeBlockContent = [];
        }
        continue;
      }

      if (inCodeBlock) {
        codeBlockContent.push(line);
        continue;
      }

      // Handle headers
      const headerMatch = line.match(/^(#{1,6})\s+(.+)$/);
      if (headerMatch) {
        const level = headerMatch[1].length;
        const text = headerMatch[2];
        // Bold blue for headers (matching Glamour)
        processedLines.push(chalk.bold.hex('#7aa2f7')(text));
        continue;
      }

      // Handle lists
      const listMatch = line.match(/^(\s*)([-*]|\d+\.)\s+(.+)$/);
      if (listMatch) {
        const indent = listMatch[1];
        const bullet = listMatch[2];
        const text = listMatch[3];
        // Process inline markdown in list items
        const processedText = processInlineMarkdown(text);
        processedLines.push(`${indent}• ${processedText}`);
        continue;
      }

      // Process inline markdown for regular lines
      processedLines.push(processInlineMarkdown(line));
    }

    return processedLines.join('\n');
  } catch (error) {
    // Fallback to plain text if markdown rendering fails
    console.error('Markdown rendering failed:', error);
    return markdown;
  }
}

/**
 * Process inline markdown elements (bold, italic, code, links)
 */
function processInlineMarkdown(text: string): string {
  let processed = text;

  // Inline code (`) - light green
  processed = processed.replace(/`([^`]+)`/g, (_, code) => {
    return chalk.hex('#9ece6a')(code);
  });

  // Bold (**text** or __text__) - bold
  processed = processed.replace(/\*\*([^*]+)\*\*/g, (_, text) => {
    return chalk.bold(text);
  });
  processed = processed.replace(/__([^_]+)__/g, (_, text) => {
    return chalk.bold(text);
  });

  // Italic (*text* or _text_) - italic
  processed = processed.replace(/\*([^*]+)\*/g, (_, text) => {
    return chalk.italic(text);
  });
  processed = processed.replace(/_([^_]+)_/g, (_, text) => {
    return chalk.italic(text);
  });

  // Links [text](url) - cyan
  processed = processed.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_, text, url) => {
    return chalk.hex('#7dcfff')(text);
  });

  return processed;
}
