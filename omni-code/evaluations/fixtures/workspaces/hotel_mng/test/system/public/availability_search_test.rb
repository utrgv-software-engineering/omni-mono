# frozen_string_literal: true

require "application_system_test_case"

module Public
  class AvailabilitySearchTest < ApplicationSystemTestCase
    setup do
      @account = create(:account)
      ActsAsTenant.current_tenant = @account
      @hotel = create(:hotel, account: @account, subdomain: "testhotel")
      @room_type = create(:room_type, hotel: @hotel, account: @account, base_price: 150.00, name: "Standard Room", max_occupancy: 4)
      create_list(:room, 3, hotel: @hotel, room_type: @room_type, status: "available", account: @account)
    end

    test "guest can update search criteria and see results" do
      visit public_availability_index_path(
        hotel_id: @hotel.id,
        check_in_date: Date.tomorrow,
        check_out_date: Date.tomorrow + 2.days,
        guests: 2
      )

      # Should see initial results
      assert_text "Available Rooms"
      assert_text @room_type.name
      assert_text "$150"
      assert_text "3 rooms available"

      # Update search criteria
      select "4 Guests", from: "guests"
      click_button "Update Search"

      # Should still see results (room type supports 4 guests)
      assert_text @room_type.name
    end
  end
end
