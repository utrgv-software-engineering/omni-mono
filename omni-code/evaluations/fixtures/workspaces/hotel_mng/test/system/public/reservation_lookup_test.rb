# frozen_string_literal: true

require "application_system_test_case"

module Public
  class ReservationLookupTest < ApplicationSystemTestCase
    setup do
      @account = create(:account)
      ActsAsTenant.current_tenant = @account
      @hotel = create(:hotel, account: @account)
      @room_type = create(:room_type, hotel: @hotel, account: @account)
      @guest = create(:guest, account: @account, email: "guest@example.com", first_name: "Jane", last_name: "Smith")
      @reservation = create(:reservation,
        account: @account,
        hotel: @hotel,
        guest: @guest,
        room_type: @room_type,
        status: "confirmed",
        check_in_date: Date.tomorrow + 7.days,
        check_out_date: Date.tomorrow + 10.days
      )
    end

    test "guest can look up reservation with confirmation number and email" do
      visit public_reservation_lookup_path(hotel_id: @hotel.id)

      # Use label text for filling in forms
      fill_in "Confirmation Number", with: @reservation.confirmation_number
      fill_in "Email Address", with: "guest@example.com"
      click_button "Find Reservation"

      assert_text @reservation.confirmation_number
      assert_text "Jane Smith"
    end

    test "guest can cancel their reservation" do
      # Go directly to reservation show page using confirmation number (as the route expects)
      visit public_reservation_path(@reservation.confirmation_number, email: "guest@example.com")

      click_link "Cancel Reservation"

      # Should be on cancel confirmation page
      assert_text "Cancel"

      click_button "Yes, Cancel Reservation"

      # Should see cancelled confirmation
      assert_text "cancelled"
    end
  end
end
