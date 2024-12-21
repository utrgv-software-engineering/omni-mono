export class StringMatcher {
  private matched: boolean;
  private onMatch: (url: string) => void;
  private filter: (data: string) => boolean;
  private re: RegExp;

  constructor(arg: { filter: StringMatcher['filter']; onMatch: StringMatcher['onMatch']; re: StringMatcher['re'] }) {
    this.filter = arg.filter;
    this.onMatch = arg.onMatch;
    this.matched = false;
    this.re = arg.re;
  }

  checkForMatch = (data: string) => {
    if (this.matched) {
      return;
    }

    if (!this.filter(data)) {
      return;
    }

    const match = data.match(this.re);

    if (!match) {
      return;
    }

    const url = match[0];
    this.matched = true;
    this.onMatch(url);
  };

  reset = () => {
    this.matched = false;
  };

  hasMatched = () => {
    return this.matched;
  };
}
