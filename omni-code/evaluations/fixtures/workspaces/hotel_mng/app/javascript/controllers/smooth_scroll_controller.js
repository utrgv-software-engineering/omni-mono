import { Controller } from "@hotwired/stimulus"

export default class extends Controller {
  connect() {
    this.element.addEventListener("click", this.handleClick.bind(this))
  }

  handleClick(event) {
    const link = event.target.closest("[data-section]")
    if (!link) return
    
    event.preventDefault()
    const targetId = link.getAttribute("href")
    const targetElement = document.querySelector(targetId)
    
    if (targetElement) {
      targetElement.scrollIntoView({ behavior: "smooth", block: "start" })
      history.pushState(null, null, targetId)
      
      // Update active state
      this.element.querySelectorAll("[data-section]").forEach(el => {
        el.classList.remove("bg-gray-100", "dark:bg-gray-700")
      })
      link.classList.add("bg-gray-100", "dark:bg-gray-700")
    }
  }
}