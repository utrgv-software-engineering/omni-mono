# Preview all emails at http://localhost:3000/rails/mailers/reservation_mailer
class ReservationMailerPreview < ActionMailer::Preview
  # Preview this email at http://localhost:3000/rails/mailers/reservation_mailer/booking_confirmation
  def booking_confirmation
    ReservationMailer.booking_confirmation
  end

  # Preview this email at http://localhost:3000/rails/mailers/reservation_mailer/modification_confirmation
  def modification_confirmation
    ReservationMailer.modification_confirmation
  end

  # Preview this email at http://localhost:3000/rails/mailers/reservation_mailer/cancellation_confirmation
  def cancellation_confirmation
    ReservationMailer.cancellation_confirmation
  end
end
