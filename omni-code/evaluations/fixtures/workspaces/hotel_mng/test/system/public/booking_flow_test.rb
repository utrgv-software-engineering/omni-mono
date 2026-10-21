# frozen_string_literal: true

require "application_system_test_case"

module Public
  class BookingFlowTest < ApplicationSystemTestCase
    setup do
      @account = create(:account)
      ActsAsTenant.current_tenant = @account
      @hotel = create(:hotel, account: @account, subdomain: "testhotel")
      @room_type = create(:room_type, hotel: @hotel, account: @account, base_price: 150.00, name: "Standard Room")
      create_list(:room, 3, hotel: @hotel, room_type: @room_type, status: "available", account: @account)
    end

    test "guest can search for available rooms" do
      visit public_availability_index_path(
        hotel_id: @hotel.id,
        check_in_date: Date.tomorrow,
        check_out_date: Date.tomorrow + 2.days,
        guests: 2
      )

      assert_text "Available Rooms"
      assert_text @room_type.name
      assert_text "$150"
    end

    test "guest can complete a booking end-to-end" do
      # Start at availability search with results
      visit public_availability_index_path(
        hotel_id: @hotel.id,
        check_in_date: Date.tomorrow,
        check_out_date: Date.tomorrow + 2.days,
        guests: 2
      )

      # Select room type
      click_link "Select Room"

      # Should be on booking form
      assert_text "Complete Your Booking"

      # Fill guest information using label text (forms use fields_for :guest)
      fill_in "First name", with: "John"
      fill_in "Last name", with: "Doe"
      fill_in "Email", with: "john@example.com"
      fill_in "Phone", with: "555-1234"

      click_button "Complete Booking"

      # Should be on confirmation page
      assert_text "Confirmed"
      assert_text "John"
    end
  end
end
