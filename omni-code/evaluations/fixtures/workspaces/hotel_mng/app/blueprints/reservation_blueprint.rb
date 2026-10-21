class ReservationBlueprint < ApplicationBlueprint
  identifier :id
  
  fields :confirmation_number, :check_in_date, :check_out_date, :actual_check_in,
         :actual_check_out, :status, :adults, :children, :infants, :total_amount,
         :paid_amount, :payment_status, :currency, :source, :special_requests,
         :internal_notes, :hotel_id, :guest_id, :room_id, :room_type_id
  
  association :guest, blueprint: GuestBlueprint
  association :room, blueprint: RoomBlueprint
  association :room_type, blueprint: RoomTypeBlueprint, view: :basic
  
  field :nights do |reservation|
    reservation.nights
  end
  
  field :balance_due do |reservation|
    reservation.balance_due
  end
  
  field :total_guests do |reservation|
    reservation.total_guests
  end
end