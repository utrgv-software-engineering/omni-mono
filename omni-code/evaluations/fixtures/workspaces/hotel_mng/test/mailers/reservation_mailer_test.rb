require "test_helper"

class ReservationMailerTest < ActionMailer::TestCase
  setup do
    @account = create(:account)
    set_current_tenant(@account)
    @hotel = create(:hotel, account: @account)
    @room_type = create(:room_type, hotel: @hotel, account: @account)
    @room = create(:room, hotel: @hotel, account: @account, room_type: @room_type)
    @guest = create(:guest, account: @account)
    @reservation = create(
      :reservation,
      account: @account,
      hotel: @hotel,
      guest: @guest,
      room_type: @room_type,
      room: @room,
      status: "confirmed"
    )
  end

  teardown do
    clear_current_tenant
  end

  test "booking_confirmation" do
    mail = ReservationMailer.booking_confirmation(@reservation)
    assert_equal "Booking Confirmation - #{@reservation.confirmation_number}", mail.subject
    assert_equal [@guest.email], mail.to
    assert_equal ["reservations@hotelmanager.com"], mail.from
    assert_includes mail.body.encoded, @guest.first_name
  end

  test "modification_confirmation" do
    mail = ReservationMailer.modification_confirmation(@reservation)
    assert_equal "Reservation Modified - #{@reservation.confirmation_number}", mail.subject
    assert_equal [@guest.email], mail.to
    assert_equal ["reservations@hotelmanager.com"], mail.from
    assert_includes mail.body.encoded, @hotel.name
  end

  test "cancellation_confirmation" do
    @reservation.update!(status: "cancelled")
    mail = ReservationMailer.cancellation_confirmation(@reservation)
    assert_equal "Reservation Cancelled - #{@reservation.confirmation_number}", mail.subject
    assert_equal [@guest.email], mail.to
    assert_equal ["reservations@hotelmanager.com"], mail.from
    assert_includes mail.body.encoded, @reservation.confirmation_number
  end
end
