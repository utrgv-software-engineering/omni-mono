export class SlidingBuffer<T> {
  private buffer: T[];
  private size: number;

  constructor(size: number, initial?: T[]) {
    this.size = size;
    this.buffer = initial ?? [];
  }

  push(value: T) {
    this.buffer.push(value);
    if (this.buffer.length > this.size) {
      this.buffer.shift();
    }
  }

  get() {
    return this.buffer;
  }

  clear() {
    this.buffer = [];
  }
}
