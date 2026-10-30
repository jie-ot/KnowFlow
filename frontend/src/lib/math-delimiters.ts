/** Accept common model LaTeX delimiters without changing code or dollar math. */
export function normalizeMathDelimiters(text: string): string {
  return text.replace(
    /(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`|\$\$[\s\S]*?\$\$|\$[^$\n]*\$)|\\\[([\s\S]*?)\\\]|\\\(([^\n]*?)\\\)/g,
    (whole, protectedText: string | undefined, block: string | undefined, inline: string | undefined) => {
      if (protectedText !== undefined) return whole;
      if (block !== undefined) return `\n\n$$\n${block.trim()}\n$$\n\n`;
      return `$${inline?.trim()}$`;
    },
  );
}
