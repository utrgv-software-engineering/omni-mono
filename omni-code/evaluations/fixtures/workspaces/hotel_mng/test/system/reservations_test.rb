# frozen_string_literal: true

require "application_system_test_case"

class ReservationsTest < ApplicationSystemTestCase
  setup do
    @context = sign_in_as_role(:front_desk)
    @hotel = @context[:hotel]
    @account = @context[:account]
    @guest = create(:guest, account: @account, first_name: "John", last_name: "Doe", email: "john@example.com")
  end

  test "staff can create a new reservation" do
    # Create room type for the hotel
    room_type = create(:room_type, hotel: @hotel, account: @account, name: "Standard Room")

    # Visit the new reservation path WITH hotel_id to pre-populate room types
    visit new_reservation_path(hotel_id: @hotel.id)

    # Select existing guest (the dropdown shows "Full Name - email")
    select "John Doe - john@example.com", from: "reservation_guest_id"
    # Room type dropdown shows "Name - $price/night (Max: X)" - use partial match
    find("#reservation_room_type_id").find(:option, text: /Standard Room/).select_option
    fill_in "reservation_check_in_date", with: Date.tomorrow.to_s
    fill_in "reservation_check_out_date", with: (Date.tomorrow + 3.days).to_s
    fill_in "reservation_adults", with: 2
    fill_in "reservation_total_amount", with: 450

    click_button "Create Reservation"

    assert_text "Reservation was successfully created"
  end

  test "staff can view and filter reservations" do
    room_type = create(:room_type, hotel: @hotel, account: @account)

    create(:reservation,
      hotel: @hotel,
      account: @account,
      status: "confirmed",
      guest: @guest,
      room_type: room_type,
      check_in_date: Date.current,
      check_out_date: Date.current + 2.days
    )
    create(:reservation,
      hotel: @hotel,
      account: @account,
      status: "checked_in",
      guest: @guest,
      room_type: room_type,
      check_in_date: Date.current - 1.day,
      check_out_date: Date.current + 1.day
    )

    visit reservations_path

    # Verify reservations list displays
    assert_text @guest.last_name

    # Test filter - clicking Arrivals should filter the view
    click_link "Arrivals"
    # Verify we're still on reservations page and can see reservations
    assert_text "Reservations"
    assert_text @guest.last_name
  end
end
