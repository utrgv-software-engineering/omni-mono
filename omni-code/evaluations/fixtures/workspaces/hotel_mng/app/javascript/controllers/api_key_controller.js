import { Controller } from "@hotwired/stimulus"

export default class extends Controller {
  static targets = ["buttonText"]
  static values = { apiKey: String }

  copy(event) {
    event.preventDefault()
    
    // Copy to clipboard
    navigator.clipboard.writeText(this.apiKeyValue).then(() => {
      // Update button text temporarily
      const originalText = this.buttonTextTarget.textContent
      this.buttonTextTarget.textContent = "Copied!"
      
      // Add a success style
      event.currentTarget.classList.add("text-green-600")
      event.currentTarget.classList.remove("text-indigo-600")
      
      // Reset after 2 seconds
      setTimeout(() => {
        this.buttonTextTarget.textContent = originalText
        event.currentTarget.classList.remove("text-green-600")
        event.currentTarget.classList.add("text-indigo-600")
      }, 2000)
    }).catch(err => {
      console.error('Failed to copy API key:', err)
      // Fallback for browsers that don't support clipboard API
      this.fallbackCopy()
    })
  }

  fallbackCopy() {
    // Create a temporary input element
    const tempInput = document.createElement("input")
    tempInput.style.position = "absolute"
    tempInput.style.left = "-9999px"
    tempInput.value = this.apiKeyValue
    document.body.appendChild(tempInput)
    
    // Select and copy
    tempInput.select()
    tempInput.setSelectionRange(0, 99999) // For mobile devices
    
    try {
      document.execCommand("copy")
      this.buttonTextTarget.textContent = "Copied!"
      setTimeout(() => {
        this.buttonTextTarget.textContent = "Copy API Key"
      }, 2000)
    } catch (err) {
      console.error('Fallback copy failed:', err)
      this.buttonTextTarget.textContent = "Copy failed"
    }
    
    // Remove temporary input
    document.body.removeChild(tempInput)
  }
}