import ansiRegex from 'ansi-regex';

type AppendResult = { complete: string; hasIncomplete: false } | { complete: null; hasIncomplete: true };

export class AnsiSequenceBuffer {
  private buffer = '';
  private regex = ansiRegex();
  private readonly ANSI_ESC = '\x1b[';

  append(data: string): AppendResult {
    const newData = this.buffer + data;

    if (this.hasIncompleteAnsi(newData)) {
      this.buffer = newData;
      return { complete: null, hasIncomplete: true };
    }

    this.buffer = '';

    return { complete: newData, hasIncomplete: false };
  }

  clear(): void {
    this.buffer = '';
  }

  private hasIncompleteAnsi(str: string): boolean {
    const lastEsc = str.lastIndexOf(this.ANSI_ESC);
    if (lastEsc === -1) {
      return false;
    }
    return !str.slice(lastEsc).match(this.regex);
  }
}
