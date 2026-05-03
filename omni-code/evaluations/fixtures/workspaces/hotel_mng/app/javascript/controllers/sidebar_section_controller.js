import { Controller } from "@hotwired/stimulus"

export default class extends Controller {
  static targets = ["content", "chevron"]
  static values = { collapsed: Boolean }

  connect() {
    if (this.hasCollapsedValue && this.collapsedValue) {
      this.contentTarget.classList.add("hidden")
      this.chevronTarget.classList.remove("rotate-90")
    }
  }

  toggle() {
    this.contentTarget.classList.toggle("hidden")
    this.chevronTarget.classList.toggle("rotate-90")
  }
}