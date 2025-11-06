/**
 * Smart tool argument formatting - matches Bubbletea implementation
 * Formats as: key1: "value1", key2: 42, key3: true
 */

export function formatToolArgumentsSmart(argsStr: string, availableWidth: number = 50): string {
  // Minimum width to show anything useful
  if (availableWidth < 10) {
    return '...';
  }

  try {
    // Try to parse as JSON object
    const argsObj = JSON.parse(argsStr);

    if (typeof argsObj !== 'object' || argsObj === null || Array.isArray(argsObj)) {
      // Not a plain object, use simple truncation
      return argsStr.length <= availableWidth ? argsStr : argsStr.slice(0, availableWidth - 3) + '...';
    }

    const parts: string[] = [];
    const keys = Object.keys(argsObj);
    let currentLen = 0;
    const ellipsisLen = 3; // "..."
    const commaSpaceLen = 2; // ", "

    for (let i = 0; i < keys.length; i++) {
      const key = keys[i];
      const value = argsObj[key];

      // Format value based on type
      let valueStr: string;
      if (typeof value === 'string') {
        // Quote strings
        valueStr = JSON.stringify(value);
      } else if (typeof value === 'number') {
        // Format numbers
        valueStr = Number.isInteger(value) ? value.toString() : value.toString();
      } else if (typeof value === 'boolean') {
        valueStr = value.toString();
      } else if (value === null) {
        valueStr = 'null';
      } else {
        // Complex types (arrays, objects) - serialize as JSON
        valueStr = JSON.stringify(value);
      }

      const fullArg = `${key}: ${valueStr}`;
      const argLen = fullArg.length + (i > 0 ? commaSpaceLen : 0);

      // Check if we can fit this argument
      if (currentLen + argLen <= availableWidth) {
        parts.push(fullArg);
        currentLen += argLen;
      } else {
        // Can't fit completely
        const remainingSpace = availableWidth - currentLen - (i > 0 ? commaSpaceLen : 0);
        const minArgLen = key.length + 2 + 4 + ellipsisLen; // "key: X..."

        if (i === 0 && remainingSpace >= minArgLen) {
          // Truncate the first argument if it's the only one
          const maxValueLen = remainingSpace - key.length - 2 - ellipsisLen;
          if (maxValueLen > 0) {
            let truncatedValue = valueStr;
            if (valueStr.length > maxValueLen) {
              // Handle quoted strings specially
              if (valueStr.startsWith('"') && valueStr.endsWith('"')) {
                const innerContent = valueStr.slice(1, -1);
                if (innerContent.length > maxValueLen - 2) {
                  truncatedValue = `"${innerContent.slice(0, maxValueLen - 5)}..."`;
                }
              } else {
                truncatedValue = valueStr.slice(0, maxValueLen) + '...';
              }
            }
            parts.push(`${key}: ${truncatedValue}`);
          } else {
            parts.push('...');
          }
        } else if (parts.length > 0) {
          // We have some args already, add ellipsis
          if (currentLen + commaSpaceLen + ellipsisLen <= availableWidth) {
            parts.push('...');
          }
        } else if (remainingSpace >= key.length + 6) {
          // Try to show "key: ..."
          parts.push(`${key}: ...`);
        } else {
          // Nothing fits
          parts.push('...');
        }
        break;
      }
    }

    return parts.join(', ');
  } catch (e) {
    // Not valid JSON, use simple truncation
    return argsStr.length <= availableWidth ? argsStr : argsStr.slice(0, availableWidth - 3) + '...';
  }
}
