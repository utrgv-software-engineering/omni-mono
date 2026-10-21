# frozen_string_literal: true

require "test_helper"

module Public
  class BookingsControllerTest < ActionDispatch::IntegrationTest
    setup do
      @account = create(:account)
      ActsAsTenant.current_tenant = @account
      @hotel = create(:hotel, account: @account, subdomain: "testhotel")
      @room_type = create(:room_type, hotel: @hotel, account: @account, base_price: 150.00)
    end

    teardown do
      ActsAsTenant.current_tenant = nil
    end

    # Helper to simulate subdomain request
    def public_booking_url(path, hotel: @hotel)
      # Use hotel_id param as fallback since subdomain simulation is complex in tests
      "#{path}?hotel_id=#{hotel.id}"
    end

    # ===========================================
    # New Booking Form Tests
    # ===========================================

    test "new displays booking form with room type" do
      get "/public/bookings/new", params: {
        hotel_id: @hotel.id,
        room_type_id: @room_type.id,
        check_in_date: Date.tomorrow.to_s,
        check_out_date: (Date.tomorrow + 2.days).to_s,
        guests: 2
      }
      assert_response :success
      assert_select "form"
    end

    test "new calculates total amount from dates and room type" do
      check_in = Date.tomorrow
      check_out = Date.tomorrow + 3.days # 3 nights

      get "/public/bookings/new", params: {
        hotel_id: @hotel.id,
        room_type_id: @room_type.id,
        check_in_date: check_in.to_s,
        check_out_date: check_out.to_s,
        guests: 2
      }

      assert_response :success
      # 3 nights * $150 = $450
      assert_select "main", /450/
    end

    test "new requires hotel_id or subdomain" do
      # Without hotel_id, should redirect with flash
      get "/public/bookings/new", params: { room_type_id: @room_type.id }
      assert_response :redirect
      assert_match(/hotel not found/i, flash[:alert])
    end

    # ===========================================
    # Create Booking Tests
    # ===========================================

    test "create booking with valid params creates reservation" do
      assert_difference "Reservation.count", 1 do
        post "/public/bookings", params: {
          hotel_id: @hotel.id,
          room_type_id: @room_type.id,
          reservation: {
            check_in_date: Date.tomorrow,
            check_out_date: Date.tomorrow + 2.days,
            adults: 2,
            children: 0,
            special_requests: "Late check-in"
          },
          guest: {
            first_name: "John",
            last_name: "Doe",
            email: "john.doe@example.com",
            phone: "555-1234"
          }
        }
      end

      reservation = Reservation.last
      # Redirect uses hotel subdomain path when not using subdomain request
      assert_redirected_to "/hotels/#{@hotel.subdomain}/bookings/#{reservation.confirmation_number}"
      assert_equal "confirmed", reservation.status
      assert_equal "website", reservation.source
      assert reservation.confirmation_number.present?
    end

    test "create booking generates unique confirmation number" do
      post "/public/bookings", params: {
        hotel_id: @hotel.id,
        room_type_id: @room_type.id,
        reservation: {
          check_in_date: Date.tomorrow,
          check_out_date: Date.tomorrow + 2.days,
          adults: 2
        },
        guest: {
          first_name: "Jane",
          last_name: "Smith",
          email: "jane.smith@example.com"
        }
      }

      reservation = Reservation.last
      # Confirmation number should be 8 alphanumeric characters
      assert_match(/\A[A-Z0-9]{8}\z/, reservation.confirmation_number)
    end

    test "create booking calculates total amount from room type pricing" do
      post "/public/bookings", params: {
        hotel_id: @hotel.id,
        room_type_id: @room_type.id,
        reservation: {
          check_in_date: Date.tomorrow,
          check_out_date: Date.tomorrow + 3.days, # 3 nights
          adults: 2
        },
        guest: {
          first_name: "Bob",
          last_name: "Jones",
          email: "bob.jones@example.com"
        }
      }

      reservation = Reservation.last
      # Room type base_price is $150, 3 nights = $450
      assert_equal 450.00, reservation.total_amount
    end

    test "create booking sets correct defaults" do
      post "/public/bookings", params: {
        hotel_id: @hotel.id,
        room_type_id: @room_type.id,
        reservation: {
          check_in_date: Date.tomorrow,
          check_out_date: Date.tomorrow + 2.days,
          adults: 2
        },
        guest: {
          first_name: "Alice",
          last_name: "Wonder",
          email: "alice@example.com"
        }
      }

      reservation = Reservation.last
      assert_equal "confirmed", reservation.status
      assert_equal "website", reservation.source
      assert_equal "pending", reservation.payment_status
      assert_equal 0, reservation.paid_amount
      assert_equal 0, reservation.children
      assert_equal 0, reservation.infants
    end

    test "create booking creates records in correct account" do
      post "/public/bookings", params: {
        hotel_id: @hotel.id,
        room_type_id: @room_type.id,
        reservation: {
          check_in_date: Date.tomorrow,
          check_out_date: Date.tomorrow + 2.days,
          adults: 1
        },
        guest: {
          first_name: "Test",
          last_name: "Guest",
          email: "test@example.com"
        }
      }

      reservation = Reservation.last
      guest = Guest.last

      assert_equal @account, reservation.account
      assert_equal @account, guest.account
      assert_equal @hotel, reservation.hotel
    end

    test "create booking finds existing guest by email" do
      # Create existing guest in this account
      existing_guest = create(:guest, account: @account, email: "existing@example.com", first_name: "Old", last_name: "Name")

      assert_no_difference "Guest.count" do
        post "/public/bookings", params: {
          hotel_id: @hotel.id,
          room_type_id: @room_type.id,
          reservation: {
            check_in_date: Date.tomorrow,
            check_out_date: Date.tomorrow + 2.days,
            adults: 1
          },
          guest: {
            first_name: "New",
            last_name: "Name",
            email: "existing@example.com"
          }
        }
      end

      reservation = Reservation.last
      assert_equal existing_guest.id, reservation.guest_id

      # Guest name should be updated
      existing_guest.reload
      assert_equal "New", existing_guest.first_name
      assert_equal "Name", existing_guest.last_name
    end

    test "create booking creates new guest if email not found" do
      assert_difference "Guest.count", 1 do
        post "/public/bookings", params: {
          hotel_id: @hotel.id,
          room_type_id: @room_type.id,
          reservation: {
            check_in_date: Date.tomorrow,
            check_out_date: Date.tomorrow + 2.days,
            adults: 1
          },
          guest: {
            first_name: "Brand",
            last_name: "New",
            email: "brandnew@example.com"
          }
        }
      end

      guest = Guest.last
      assert_equal "Brand", guest.first_name
      assert_equal "New", guest.last_name
      assert_equal "brandnew@example.com", guest.email
    end

    test "create booking with invalid params renders form with errors" do
      assert_no_difference "Reservation.count" do
        post "/public/bookings", params: {
          hotel_id: @hotel.id,
          room_type_id: @room_type.id,
          reservation: {
            check_in_date: Date.tomorrow,
            check_out_date: Date.yesterday, # Invalid: before check_in
            adults: 2
          },
          guest: {
            first_name: "Invalid",
            last_name: "Booking",
            email: "invalid@example.com"
          }
        }
      end

      assert_response :unprocessable_content
    end

    # ===========================================
    # Show Booking (Confirmation Page) Tests
    # ===========================================

    test "show displays booking confirmation by confirmation number" do
      guest = create(:guest, account: @account)
      reservation = create(:reservation, account: @account, hotel: @hotel, guest: guest, room_type: @room_type, status: "confirmed")

      # Show action still needs hotel context for layout
      get "/public/bookings/#{reservation.confirmation_number}", params: { hotel_id: @hotel.id }
      assert_response :success
      assert_select "main", /#{reservation.confirmation_number}/
    end

    test "show with invalid confirmation number returns not found" do
      get "/public/bookings/INVALID123", params: { hotel_id: @hotel.id }
      assert_response :not_found
    end

    # ===========================================
    # Multi-tenancy Tests
    # ===========================================

    test "booking is scoped to correct hotel account" do
      # Create another account with different hotel
      other_account = create(:account)
      other_hotel = nil
      other_room_type = nil

      ActsAsTenant.with_tenant(other_account) do
        other_hotel = create(:hotel, account: other_account, subdomain: "otherhotel")
        other_room_type = create(:room_type, hotel: other_hotel, account: other_account)
      end

      # Book at other hotel
      post "/public/bookings", params: {
        hotel_id: other_hotel.id,
        room_type_id: other_room_type.id,
        reservation: {
          check_in_date: Date.tomorrow,
          check_out_date: Date.tomorrow + 2.days,
          adults: 1
        },
        guest: {
          first_name: "Cross",
          last_name: "Tenant",
          email: "cross@example.com"
        }
      }

      reservation = Reservation.last
      assert_equal other_account, reservation.account
      assert_equal other_hotel, reservation.hotel
    end

    # ===========================================
    # Edge Cases
    # ===========================================

    test "create booking with special requests" do
      post "/public/bookings", params: {
        hotel_id: @hotel.id,
        room_type_id: @room_type.id,
        reservation: {
          check_in_date: Date.tomorrow,
          check_out_date: Date.tomorrow + 2.days,
          adults: 2,
          special_requests: "Ground floor room, extra pillows"
        },
        guest: {
          first_name: "Special",
          last_name: "Guest",
          email: "special@example.com"
        }
      }

      reservation = Reservation.last
      assert_equal "Ground floor room, extra pillows", reservation.special_requests
    end

    test "create booking with children and infants" do
      post "/public/bookings", params: {
        hotel_id: @hotel.id,
        room_type_id: @room_type.id,
        reservation: {
          check_in_date: Date.tomorrow,
          check_out_date: Date.tomorrow + 2.days,
          adults: 2,
          children: 2,
          infants: 1
        },
        guest: {
          first_name: "Family",
          last_name: "Traveler",
          email: "family@example.com"
        }
      }

      reservation = Reservation.last
      assert_equal 2, reservation.adults
      assert_equal 2, reservation.children
      assert_equal 1, reservation.infants
    end
  end
end
