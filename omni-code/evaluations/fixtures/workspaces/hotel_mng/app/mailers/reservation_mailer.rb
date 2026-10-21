class ReservationMailer < ApplicationMailer
  default from: 'reservations@hotelmanager.com'
  
  def booking_confirmation(reservation)
    @reservation = reservation
    @guest = reservation.guest
    @hotel = reservation.hotel
    @room_type = reservation.room_type
    
    mail(
      to: @guest.email,
      subject: "Booking Confirmation - #{@reservation.confirmation_number}"
    )
  end

  def modification_confirmation(reservation)
    @reservation = reservation
    @guest = reservation.guest
    @hotel = reservation.hotel
    @room_type = reservation.room_type
    
    mail(
      to: @guest.email,
      subject: "Reservation Modified - #{@reservation.confirmation_number}"
    )
  end

  def cancellation_confirmation(reservation)
    @reservation = reservation
    @guest = reservation.guest
    @hotel = reservation.hotel
    @room_type = reservation.room_type
    @cancellation_fee = reservation.calculate_cancellation_fee
    @refund_amount = reservation.refundable_amount
    
    mail(
      to: @guest.email,
      subject: "Reservation Cancelled - #{@reservation.confirmation_number}"
    )
  end
end