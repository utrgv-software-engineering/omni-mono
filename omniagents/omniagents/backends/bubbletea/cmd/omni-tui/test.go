package main

import (
	"fmt"
)

func testNewlines() {
	preview := "def main():\n    print(\"Hello, world!\")\n\n\nif __name__ == \"__main__\":\n    main()"
	codeBlock := fmt.Sprintf("```python\n%s\n```", preview)

	fmt.Println("Preview string:")
	fmt.Println(preview)
	fmt.Println("\nFormatted code block:")
	fmt.Println(codeBlock)
}
