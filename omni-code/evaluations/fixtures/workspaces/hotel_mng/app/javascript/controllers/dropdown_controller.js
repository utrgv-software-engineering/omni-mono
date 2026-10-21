import { Controller } from "@hotwired/stimulus"

export default class extends Controller {
  static targets = ["menu"]
  
  connect() {
    this.open = false
  }

  toggle() {
    this.open = !this.open
    if (this.open) {
      this.menuTarget.classList.remove("hidden")
    } else {
      this.menuTarget.classList.add("hidden")
    }
  }

  hide(event) {
    if (!this.element.contains(event.target)) {
      this.open = false
      this.menuTarget.classList.add("hidden")
    }
  }
}