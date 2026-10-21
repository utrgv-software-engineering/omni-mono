import { Controller } from "@hotwired/stimulus"

// Connects to data-controller="property-card"
export default class extends Controller {
  static targets = ["image", "favoriteButton"]
  
  connect() {
    this.currentSlide = 0
    this.isFavorited = false
  }
  
  toggleFavorite(event) {
    event.preventDefault()
    event.stopPropagation()
    
    this.isFavorited = !this.isFavorited
    const button = this.favoriteButtonTarget
    const svg = button.querySelector('svg')
    
    if (this.isFavorited) {
      svg.classList.add('fill-red-500', 'stroke-red-500')
      svg.classList.remove('fill-transparent')
      
      // Add heart animation
      button.classList.add('animate-pulse')
      setTimeout(() => {
        button.classList.remove('animate-pulse')
      }, 300)
    } else {
      svg.classList.remove('fill-red-500', 'stroke-red-500')
      svg.classList.add('fill-transparent')
    }
    
    // Here you would typically make an API call to save the favorite
    console.log('Favorite toggled:', this.isFavorited)
  }
  
  goToSlide(event) {
    event.preventDefault()
    event.stopPropagation()
    
    const slideIndex = parseInt(event.currentTarget.dataset.slideIndex)
    this.currentSlide = slideIndex
    
    // Update dots
    const dots = this.element.querySelectorAll('[data-slide-index]')
    dots.forEach((dot, index) => {
      if (index === slideIndex) {
        dot.classList.remove('bg-white/50')
        dot.classList.add('bg-white')
      } else {
        dot.classList.add('bg-white/50')
        dot.classList.remove('bg-white')
      }
    })
    
    // Here you would update the actual image
    console.log('Navigate to slide:', slideIndex)
  }
  
  nextSlide(event) {
    if (event) {
      event.preventDefault()
      event.stopPropagation()
    }
    
    const totalSlides = 5 // This should be dynamic based on actual images
    this.currentSlide = (this.currentSlide + 1) % totalSlides
    this.updateSlide()
  }
  
  previousSlide(event) {
    if (event) {
      event.preventDefault()
      event.stopPropagation()
    }
    
    const totalSlides = 5
    this.currentSlide = (this.currentSlide - 1 + totalSlides) % totalSlides
    this.updateSlide()
  }
  
  updateSlide() {
    // Update the image and dots based on currentSlide
    const dots = this.element.querySelectorAll('[data-slide-index]')
    dots.forEach((dot, index) => {
      if (index === this.currentSlide) {
        dot.classList.remove('bg-white/50')
        dot.classList.add('bg-white')
      } else {
        dot.classList.add('bg-white/50')
        dot.classList.remove('bg-white')
      }
    })
  }
}