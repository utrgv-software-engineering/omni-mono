import { Controller } from "@hotwired/stimulus"

// Connects to data-controller="badge"
export default class extends Controller {
  remove(event) {
    event.preventDefault()
    event.stopPropagation()
    
    // Add fade out animation
    this.element.style.transition = 'opacity 200ms ease-out, transform 200ms ease-out'
    this.element.style.opacity = '0'
    this.element.style.transform = 'scale(0.8)'
    
    // Remove the element after animation
    setTimeout(() => {
      this.element.remove()
      
      // Dispatch a custom event that parent components can listen to
      this.dispatch('removed', { 
        detail: { 
          text: this.element.textContent.trim() 
        } 
      })
    }, 200)
  }
}