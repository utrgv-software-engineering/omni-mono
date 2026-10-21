import { Controller } from "@hotwired/stimulus"

export default class extends Controller {
  static targets = [
    "humanFields", "aiFields",
    "humanTitle", "aiTitle",
    "humanDescription", "aiDescription",
    "humanEmail", "humanFirstName", "humanLastName",
    "agentName", "submitButton"
  ]

  connect() {
    this.toggle()
  }

  toggle() {
    const userType = document.querySelector('input[name="user_type"]:checked').value
    
    if (userType === 'ai_agent') {
      // Show AI fields, hide human fields
      this.humanFieldsTarget.classList.add('hidden')
      this.aiFieldsTarget.classList.remove('hidden')
      
      this.humanTitleTarget.classList.add('hidden')
      this.aiTitleTarget.classList.remove('hidden')
      
      this.humanDescriptionTarget.classList.add('hidden')
      this.aiDescriptionTarget.classList.remove('hidden')
      
      // Update submit button text
      this.submitButtonTarget.value = 'Create AI Agent'
      
      // Clear and disable human fields
      this.humanEmailTarget.removeAttribute('required')
      this.humanEmailTarget.value = ''
      this.humanFirstNameTarget.value = ''
      this.humanLastNameTarget.value = ''
      
      // Make agent name required
      this.agentNameTarget.setAttribute('required', true)
    } else {
      // Show human fields, hide AI fields
      this.humanFieldsTarget.classList.remove('hidden')
      this.aiFieldsTarget.classList.add('hidden')
      
      this.humanTitleTarget.classList.remove('hidden')
      this.aiTitleTarget.classList.add('hidden')
      
      this.humanDescriptionTarget.classList.remove('hidden')
      this.aiDescriptionTarget.classList.add('hidden')
      
      // Update submit button text
      this.submitButtonTarget.value = 'Send Invitation'
      
      // Make email required
      this.humanEmailTarget.setAttribute('required', true)
      
      // Clear and disable AI fields
      this.agentNameTarget.removeAttribute('required')
      this.agentNameTarget.value = ''
    }
  }
}