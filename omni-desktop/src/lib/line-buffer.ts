import stripAnsi from 'strip-ansi';

export class LineBuffer {
  private buffer: string;
  private stripAnsi: boolean;

  constructor(arg?: { stripAnsi?: boolean }) {
    this.stripAnsi = arg?.stripAnsi ?? false;
    this.buffer = '';
  }

  prepData(data: string): string {
    return this.stripAnsi ? stripAnsi(data) : data;
  }

  append(data: string): string[] {
    this.buffer += this.prepData(data);
    const lines = this.buffer.split('\n');

    // Keep the last line in the buffer (it may be incomplete)
    this.buffer = lines.pop() || '';
    return lines;
  }

  flush(): string {
    const remaining = this.buffer;
    this.buffer = '';
    return remaining;
  }
}
