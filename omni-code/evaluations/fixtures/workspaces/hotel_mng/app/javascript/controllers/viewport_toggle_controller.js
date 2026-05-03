import { Controller } from "@hotwired/stimulus"

export default class extends Controller {
  connect() {
    this.container = document.getElementById("viewport-container")
  }

  setSize(event) {
    const button = event.currentTarget
    const viewport = button.dataset.viewport
    
    // Remove all existing viewport classes
    this.container.classList.remove("max-w-sm", "max-w-md", "max-w-7xl", "mx-auto")
    
    // Update button states
    this.element.querySelectorAll("button").forEach(btn => {
      btn.classList.remove("bg-gray-200", "dark:bg-gray-700")
    })
    button.classList.add("bg-gray-200", "dark:bg-gray-700")
    
    // Apply viewport-specific classes
    switch(viewport) {
      case "mobile":
        this.container.classList.add("max-w-sm", "mx-auto")
        break
      case "tablet":
        this.container.classList.add("max-w-md", "mx-auto")
        break
      case "desktop":
        this.container.classList.add("max-w-7xl", "mx-auto")
        break
    }
  }
}