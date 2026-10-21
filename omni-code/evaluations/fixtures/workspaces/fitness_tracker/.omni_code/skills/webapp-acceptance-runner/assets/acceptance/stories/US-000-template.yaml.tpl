id: US-000-template
name: Template story
criteria:
  - First acceptance criterion
selectors:
  some_button: "[data-testid='some-button']"
steps:
  - expectVisible: some_button
  - click: some_button
  - screenshot: { key: "01-after-click", label: "After click" }
