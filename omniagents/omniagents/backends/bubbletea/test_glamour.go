package main

import (
    "fmt"
    "github.com/charmbracelet/glamour"
    "strings"
)

func main() {
    customStyle := `{
        "document": {
            "block_prefix": "",
            "block_suffix": "\n",
            "color": "252",
            "margin": 0
        },
        "code_block": {
            "color": "244",
            "margin": 0
        }
    }`
    
    renderer, _ := glamour.NewTermRenderer(
        glamour.WithStylesFromJSONBytes([]byte(customStyle)),
        glamour.WithWordWrap(0),
    )
    
    code := "def main():\n    print(\"Hello, world!\")\n\n\nif __name__ == \"__main__\":\n    main()"
    markdown := "```python\n" + code + "\n```"
    
    fmt.Println("=== Original code (should have 2 blank lines): ===")
    fmt.Println(code)
    
    fmt.Println("\n=== Glamour rendered: ===")
    rendered, _ := renderer.Render(markdown)
    fmt.Println(rendered)
    
    fmt.Println("=== Counting newlines: ===")
    fmt.Printf("Original: %d newlines\n", strings.Count(code, "\n"))
    fmt.Printf("Rendered: %d newlines\n", strings.Count(rendered, "\n"))
}
