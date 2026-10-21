import { Controller } from "@hotwired/stimulus"

// Connects to data-controller="search-bar"
export default class extends Controller {
  static targets = [
    "locationInput", 
    "checkInDisplay", 
    "checkOutDisplay", 
    "guestDisplay"
  ]
  
  connect() {
    this.checkInDate = null
    this.checkOutDate = null
    this.guests = { adults: 1, children: 0, rooms: 1 }
  }
  
  onLocationInput(event) {
    const value = event.target.value
    
    // Here you would typically show location suggestions
    if (value.length > 2) {
      console.log('Searching for locations:', value)
      // Show dropdown with suggestions
    }
  }
  
  openDatePicker(event) {
    event.preventDefault()
    console.log('Opening date picker')
    
    // You would integrate with a date picker library here
    // For now, we'll simulate selecting dates
    const today = new Date()
    const tomorrow = new Date(today)
    tomorrow.setDate(tomorrow.getDate() + 1)
    const dayAfter = new Date(tomorrow)
    dayAfter.setDate(dayAfter.getDate() + 1)
    
    this.checkInDate = tomorrow
    this.checkOutDate = dayAfter
    
    if (this.hasCheckInDisplayTarget) {
      this.checkInDisplayTarget.textContent = this.formatDate(this.checkInDate)
    }
    if (this.hasCheckOutDisplayTarget) {
      this.checkOutDisplayTarget.textContent = this.formatDate(this.checkOutDate)
    }
  }
  
  openGuestSelector(event) {
    event.preventDefault()
    console.log('Opening guest selector')
    
    // You would show a dropdown/modal here
    // For demonstration, we'll just update the display
    this.updateGuestDisplay()
  }
  
  openFilters(event) {
    event.preventDefault()
    console.log('Opening filters panel')
    
    // Dispatch custom event that could be handled by a modal controller
    this.dispatch('open-filters', { detail: { filters: {} } })
  }
  
  openMobileSearch(event) {
    event.preventDefault()
    console.log('Opening mobile search modal')
    
    // Open full-screen mobile search
    this.dispatch('open-mobile-search')
  }
  
  search(event) {
    event.preventDefault()
    
    const searchData = {
      location: this.hasLocationInputTarget ? this.locationInputTarget.value : '',
      checkIn: this.checkInDate,
      checkOut: this.checkOutDate,
      guests: this.guests
    }
    
    console.log('Searching with:', searchData)
    
    // Dispatch search event or make API call
    this.dispatch('search', { detail: searchData })
    
    // Navigate to search results
    // window.location.href = `/search?${new URLSearchParams(searchData)}`
  }
  
  updateGuestDisplay() {
    if (!this.hasGuestDisplayTarget) return
    
    const { adults, children, rooms } = this.guests
    let display = `${adults} ${adults === 1 ? 'adult' : 'adults'}`
    
    if (children > 0) {
      display += `, ${children} ${children === 1 ? 'child' : 'children'}`
    }
    
    if (rooms > 1) {
      display += `, ${rooms} rooms`
    }
    
    this.guestDisplayTarget.textContent = display
  }
  
  formatDate(date) {
    if (!date) return 'Add dates'
    
    const options = { month: 'short', day: 'numeric' }
    return date.toLocaleDateString('en-US', options)
  }
  
  // Methods for handling guest selection
  incrementAdults() {
    this.guests.adults = Math.min(this.guests.adults + 1, 20)
    this.updateGuestDisplay()
  }
  
  decrementAdults() {
    this.guests.adults = Math.max(this.guests.adults - 1, 1)
    this.updateGuestDisplay()
  }
  
  incrementChildren() {
    this.guests.children = Math.min(this.guests.children + 1, 10)
    this.updateGuestDisplay()
  }
  
  decrementChildren() {
    this.guests.children = Math.max(this.guests.children - 1, 0)
    this.updateGuestDisplay()
  }
  
  incrementRooms() {
    this.guests.rooms = Math.min(this.guests.rooms + 1, 10)
    this.updateGuestDisplay()
  }
  
  decrementRooms() {
    this.guests.rooms = Math.max(this.guests.rooms - 1, 1)
    this.updateGuestDisplay()
  }
}