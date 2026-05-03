import { Controller } from "@hotwired/stimulus"
import consumer from "channels/consumer"
import { marked } from "marked"

export default class extends Controller {
  static targets = ["window", "messages", "input", "bubble", "typingIndicator"]
  static values = { 
    hotelId: Number, 
    referrer: String,
    deploymentType: String,
    deploymentKey: String
  }
  
  connect() {
    this.isOpen = false
    this.sessionId = null // Don't persist session across page loads
    this.subscription = null
    this.isTyping = false
    
    // Configure marked options
    marked.setOptions({
      breaks: true,
      gfm: true,
      headerIds: false,
      mangle: false
    })
  }

  disconnect() {
    if (this.subscription) {
      this.subscription.unsubscribe()
    }
  }

  toggle() {
    this.isOpen = !this.isOpen
    
    if (this.isOpen) {
      this.windowTarget.classList.remove("hidden")
      this.windowTarget.classList.add("flex")
      this.bubbleTarget.classList.add("hidden")
      
      if (!this.subscription) {
        this.connectToChannel()
      }
      
      // Focus input
      setTimeout(() => this.inputTarget.focus(), 100)
    } else {
      this.windowTarget.classList.add("hidden")
      this.windowTarget.classList.remove("flex")
      this.bubbleTarget.classList.remove("hidden")
    }
  }

  close() {
    this.isOpen = false
    this.windowTarget.classList.add("hidden")
    this.windowTarget.classList.remove("flex")
    this.bubbleTarget.classList.remove("hidden")
  }

  connectToChannel() {
    this.subscription = consumer.subscriptions.create(
      { 
        channel: "AgentChatChannel",
        session_id: this.sessionId,
        hotel_id: this.hotelIdValue,
        referrer: this.referrerValue,
        deployment_type: this.deploymentTypeValue,
        deployment_key: this.deploymentKeyValue
      },
      {
        connected: () => {
          console.log("Connected to AgentChatChannel")
        },

        disconnected: () => {
          console.log("Disconnected from AgentChatChannel")
        },

        received: (data) => {
          this.handleReceivedData(data)
        },

        sendMessage: function(message) {
          return this.perform('send_message', { message: message })
        },

        sendTyping: function(typing) {
          return this.perform('typing', { typing: typing })
        }
      }
    )
  }

  handleReceivedData(data) {
    switch(data.type) {
      case "connected":
        this.sessionId = data.session_id
        // Don't persist session ID - each page load should start fresh
        break
      
      case "message":
        this.addMessage(data.content, data.role)
        this.hideTypingIndicator()
        break
      
      case "tool_called":
        this.showTypingIndicator(`Using ${data.tool}...`)
        break
      
      case "tool_result":
        this.showTypingIndicator("Processing...")
        break
      
      case "run_end":
        this.hideTypingIndicator()
        break
      
      case "error":
        this.addMessage(data.message, "error")
        this.hideTypingIndicator()
        break
    }
  }

  sendMessage(event) {
    if (event.type === "submit") {
      event.preventDefault()
    }
    
    if (event.type === "keypress" && event.key !== "Enter") {
      return
    }
    
    const message = this.inputTarget.value.trim()
    if (!message) return
    
    // Add user message to chat
    this.addMessage(message, "user")
    
    // Send via ActionCable
    if (this.subscription) {
      this.subscription.sendMessage(message)
      this.showTypingIndicator()
    }
    
    // Clear input
    this.inputTarget.value = ""
  }

  addMessage(content, role) {
    const messageDiv = document.createElement("div")
    messageDiv.className = `mb-3 ${role === 'user' ? 'text-right' : ''}`
    
    const bubble = document.createElement("div")
    bubble.className = `inline-block px-4 py-3 rounded-lg max-w-xs lg:max-w-md ${
      role === 'user' 
        ? 'bg-blue-600 text-white' 
        : role === 'error'
        ? 'bg-red-100 text-red-700'
        : 'bg-gray-100 text-gray-800'
    } chat-message-content`
    
    // Parse markdown for assistant/error messages, plain text for user messages
    if (role === 'user') {
      bubble.textContent = content
    } else {
      bubble.innerHTML = marked.parse(content)
    }
    
    messageDiv.appendChild(bubble)
    this.messagesTarget.appendChild(messageDiv)
    
    // Scroll to bottom
    this.messagesTarget.scrollTop = this.messagesTarget.scrollHeight
  }

  showTypingIndicator(text = "Typing...") {
    if (this.hasTypingIndicatorTarget) {
      this.typingIndicatorTarget.textContent = text
      this.typingIndicatorTarget.classList.remove("hidden")
    }
  }

  hideTypingIndicator() {
    if (this.hasTypingIndicatorTarget) {
      this.typingIndicatorTarget.classList.add("hidden")
    }
  }

  handleInputKeyup() {
    const hasText = this.inputTarget.value.trim().length > 0
    
    if (hasText && !this.isTyping) {
      this.isTyping = true
      if (this.subscription) {
        this.subscription.sendTyping(true)
      }
    } else if (!hasText && this.isTyping) {
      this.isTyping = false
      if (this.subscription) {
        this.subscription.sendTyping(false)
      }
    }
  }
}