/**
 * A class that can be used to match a string against a regular expression and execute a callback when a match is found.
 *
 * This class is useful for parsing logs or other streams of text data and extracting URLs or other information.
 *
 * The `filter` function is used to determine if the data should be checked for a match as a performance optimization.
 */
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

  /**
   * Checks the data for a match against the regular expression. If a match is found, the `onMatch` callback is executed.
   *
   * Once a match is handled, the `matched` property is set to true and no further matches will be checked. To continue
   * checking for matches, call the `reset` method in your `onMatch` callback.
   */
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

  /**
   * Resets the matcher so that it can be used to match again.
   */
  reset = () => {
    this.matched = false;
  };

  /**
   * Returns true if a match has been found. This is reset to false when the `reset` method is called.
   */
  hasMatched = () => {
    return this.matched;
  };
}
