# frozen_string_literal: true

require "test_helper"

module Public
  class ReservationsControllerTest < ActionDispatch::IntegrationTest
    setup do
      @account = create(:account)
      ActsAsTenant.current_tenant = @account
      @hotel = create(:hotel, account: @account, subdomain: "testhotel")
      @room_type = create(:room_type, hotel: @hotel, account: @account, base_price: 150.00)
      @guest = create(:guest, account: @account, email: "guest@example.com", phone: "555-1234")
      # Reservation with future check-in (can_modify? and can_cancel? will be true)
      @reservation = create(:reservation,
        account: @account,
        hotel: @hotel,
        guest: @guest,
        room_type: @room_type,
        status: "confirmed",
        check_in_date: Date.current + 5.days,
        check_out_date: Date.current + 8.days,
        total_amount: 450.00
      )
    end

    teardown do
      ActsAsTenant.current_tenant = nil
    end

    # ===========================================
    # Lookup Tests
    # ===========================================

    test "lookup displays search form" do
      get "/public/reservations/lookup", params: { hotel_id: @hotel.id }
      assert_response :success
      assert_select "form"
    end

    # ===========================================
    # Search Tests (Verification)
    # ===========================================

    test "search requires confirmation_number" do
      post "/public/reservations/search", params: {
        hotel_id: @hotel.id,
        email: @guest.email
      }
      # Without confirmation_number, reservation won't be found
      assert_redirected_to "/hotels/#{@hotel.subdomain}/reservations/lookup"
      assert_match(/not found/i, flash[:alert])
    end

    test "search requires email or phone for verification" do
      post "/public/reservations/search", params: {
        hotel_id: @hotel.id,
        confirmation_number: @reservation.confirmation_number
      }
      # Without email or phone, verification fails
      assert_redirected_to "/hotels/#{@hotel.subdomain}/reservations/lookup"
      assert_match(/not found/i, flash[:alert])
    end

    test "search with correct confirmation_number and email redirects to show" do
      post "/public/reservations/search", params: {
        hotel_id: @hotel.id,
        confirmation_number: @reservation.confirmation_number,
        email: @guest.email
      }
      assert_response :redirect
      # Should redirect to reservation show with email param
      assert_match %r{/reservations/#{@reservation.confirmation_number}}, response.location
    end

    test "search with correct confirmation_number and phone redirects to show" do
      post "/public/reservations/search", params: {
        hotel_id: @hotel.id,
        confirmation_number: @reservation.confirmation_number,
        phone: @guest.phone
      }
      assert_response :redirect
      assert_match %r{/reservations/#{@reservation.confirmation_number}}, response.location
    end

    test "search is case-insensitive for email" do
      post "/public/reservations/search", params: {
        hotel_id: @hotel.id,
        confirmation_number: @reservation.confirmation_number,
        email: @guest.email.upcase
      }
      assert_response :redirect
      assert_match %r{/reservations/#{@reservation.confirmation_number}}, response.location
    end

    test "search with wrong email returns not found" do
      post "/public/reservations/search", params: {
        hotel_id: @hotel.id,
        confirmation_number: @reservation.confirmation_number,
        email: "wrong@example.com"
      }
      assert_redirected_to "/hotels/#{@hotel.subdomain}/reservations/lookup"
      assert_match(/not found/i, flash[:alert])
    end

    test "search with wrong phone returns not found" do
      post "/public/reservations/search", params: {
        hotel_id: @hotel.id,
        confirmation_number: @reservation.confirmation_number,
        phone: "999-9999"
      }
      assert_redirected_to "/hotels/#{@hotel.subdomain}/reservations/lookup"
      assert_match(/not found/i, flash[:alert])
    end

    test "search with invalid confirmation_number returns not found" do
      post "/public/reservations/search", params: {
        hotel_id: @hotel.id,
        confirmation_number: "INVALID123",
        email: @guest.email
      }
      assert_redirected_to "/hotels/#{@hotel.subdomain}/reservations/lookup"
      assert_match(/not found/i, flash[:alert])
    end

    # ===========================================
    # Show Tests
    # ===========================================

    test "show displays reservation details with valid email verification" do
      get "/public/reservations/#{@reservation.confirmation_number}", params: {
        hotel_id: @hotel.id,
        email: @guest.email
      }
      assert_response :success
      assert_select "main", /#{@reservation.confirmation_number}/
    end

    test "show displays reservation details with valid phone verification" do
      get "/public/reservations/#{@reservation.confirmation_number}", params: {
        hotel_id: @hotel.id,
        phone: @guest.phone
      }
      assert_response :success
    end

    test "show redirects without verification" do
      get "/public/reservations/#{@reservation.confirmation_number}", params: {
        hotel_id: @hotel.id
      }
      assert_redirected_to "/hotels/#{@hotel.subdomain}/reservations/lookup"
      assert_match(/not found/i, flash[:alert])
    end

    test "show redirects with wrong email" do
      get "/public/reservations/#{@reservation.confirmation_number}", params: {
        hotel_id: @hotel.id,
        email: "wrong@example.com"
      }
      assert_redirected_to "/hotels/#{@hotel.subdomain}/reservations/lookup"
    end

    # ===========================================
    # Edit/Modify Tests
    # ===========================================

    test "edit displays form when can_modify is true" do
      # Reservation with check_in > current + 1.day can be modified
      get "/public/reservations/#{@reservation.confirmation_number}/edit", params: {
        hotel_id: @hotel.id,
        email: @guest.email
      }
      assert_response :success
      assert_select "form"
    end

    test "edit redirects when cannot modify (checked_in)" do
      room = create(:room, hotel: @hotel, account: @account, room_type: @room_type)
      checked_in_reservation = create(:reservation, :checked_in,
        account: @account,
        hotel: @hotel,
        guest: @guest,
        room_type: @room_type,
        room: room
      )

      get "/public/reservations/#{checked_in_reservation.confirmation_number}/edit", params: {
        hotel_id: @hotel.id,
        email: @guest.email
      }
      assert_response :redirect
      assert_match(/can no longer be modified/i, flash[:alert])
    end

    test "edit redirects when cannot modify (less than 24 hours before check-in)" do
      # Reservation with check_in tomorrow (within 24 hours)
      near_reservation = create(:reservation,
        account: @account,
        hotel: @hotel,
        guest: @guest,
        room_type: @room_type,
        status: "confirmed",
        check_in_date: Date.current + 1.day,  # Tomorrow - cannot modify
        check_out_date: Date.current + 3.days
      )

      get "/public/reservations/#{near_reservation.confirmation_number}/edit", params: {
        hotel_id: @hotel.id,
        email: @guest.email
      }
      assert_response :redirect
      assert_match(/can no longer be modified/i, flash[:alert])
    end

    test "update modifies reservation with valid params" do
      new_check_out = @reservation.check_out_date + 2.days

      patch "/public/reservations/#{@reservation.confirmation_number}", params: {
        hotel_id: @hotel.id,
        email: @guest.email,
        reservation: {
          check_in_date: @reservation.check_in_date,
          check_out_date: new_check_out,
          room_type_id: @room_type.id,
          adults: 3,
          children: 1,
          special_requests: "Extra towels"
        }
      }

      assert_response :redirect
      @reservation.reload
      assert_equal new_check_out, @reservation.check_out_date
      assert_equal 3, @reservation.adults
      assert_equal 1, @reservation.children
      assert_equal "Extra towels", @reservation.special_requests
    end

    test "update recalculates total amount" do
      original_nights = (@reservation.check_out_date - @reservation.check_in_date).to_i
      new_check_out = @reservation.check_out_date + 2.days
      new_nights = (new_check_out - @reservation.check_in_date).to_i

      patch "/public/reservations/#{@reservation.confirmation_number}", params: {
        hotel_id: @hotel.id,
        email: @guest.email,
        reservation: {
          check_in_date: @reservation.check_in_date,
          check_out_date: new_check_out,
          room_type_id: @room_type.id,
          adults: 2
        }
      }

      @reservation.reload
      # New total should be nights * base_price
      expected_total = new_nights * @room_type.base_price
      assert_equal expected_total, @reservation.total_amount
    end

    test "update clears room assignment when dates change" do
      room = create(:room, hotel: @hotel, account: @account, room_type: @room_type)
      @reservation.update!(room: room)

      new_check_in = @reservation.check_in_date + 1.day

      patch "/public/reservations/#{@reservation.confirmation_number}", params: {
        hotel_id: @hotel.id,
        email: @guest.email,
        reservation: {
          check_in_date: new_check_in,
          check_out_date: @reservation.check_out_date,
          room_type_id: @room_type.id,
          adults: 2
        }
      }

      @reservation.reload
      assert_nil @reservation.room_id
    end

    test "update logs modification in internal_notes" do
      patch "/public/reservations/#{@reservation.confirmation_number}", params: {
        hotel_id: @hotel.id,
        email: @guest.email,
        reservation: {
          check_in_date: @reservation.check_in_date,
          check_out_date: @reservation.check_out_date,
          room_type_id: @room_type.id,
          adults: 2
        }
      }

      @reservation.reload
      assert_match(/Guest modified reservation online/, @reservation.internal_notes)
    end

    # ===========================================
    # Cancel Tests
    # ===========================================

    test "cancel displays cancellation page when can_cancel is true" do
      get "/public/reservations/#{@reservation.confirmation_number}/cancel", params: {
        hotel_id: @hotel.id,
        email: @guest.email
      }
      assert_response :success
    end

    test "cancel redirects when cannot cancel (checked_in)" do
      room = create(:room, hotel: @hotel, account: @account, room_type: @room_type)
      checked_in_reservation = create(:reservation, :checked_in,
        account: @account,
        hotel: @hotel,
        guest: @guest,
        room_type: @room_type,
        room: room
      )

      get "/public/reservations/#{checked_in_reservation.confirmation_number}/cancel", params: {
        hotel_id: @hotel.id,
        email: @guest.email
      }
      assert_response :redirect
      assert_match(/can no longer be cancelled/i, flash[:alert])
    end

    test "cancel redirects when cannot cancel (past check-in date)" do
      # Create reservation with past check-in date
      past_reservation = create(:reservation,
        account: @account,
        hotel: @hotel,
        guest: @guest,
        room_type: @room_type,
        status: "confirmed",
        check_in_date: Date.current - 1.day,  # Yesterday - cannot cancel
        check_out_date: Date.current + 1.day
      )

      get "/public/reservations/#{past_reservation.confirmation_number}/cancel", params: {
        hotel_id: @hotel.id,
        email: @guest.email
      }
      assert_response :redirect
      assert_match(/can no longer be cancelled/i, flash[:alert])
    end

    test "confirm_cancel updates reservation status to cancelled" do
      post "/public/reservations/#{@reservation.confirmation_number}/confirm_cancel", params: {
        hotel_id: @hotel.id,
        email: @guest.email
      }

      @reservation.reload
      assert_equal "cancelled", @reservation.status
    end

    test "confirm_cancel redirects to cancelled page" do
      post "/public/reservations/#{@reservation.confirmation_number}/confirm_cancel", params: {
        hotel_id: @hotel.id,
        email: @guest.email
      }

      assert_response :redirect
      assert_match %r{/reservations/#{@reservation.confirmation_number}/cancelled}, response.location
    end

    test "confirm_cancel logs cancellation in internal_notes" do
      post "/public/reservations/#{@reservation.confirmation_number}/confirm_cancel", params: {
        hotel_id: @hotel.id,
        email: @guest.email
      }

      @reservation.reload
      assert_match(/Guest cancelled online/, @reservation.internal_notes)
    end

    # ===========================================
    # Cancelled Confirmation Page Tests
    # ===========================================

    test "cancelled displays confirmation for cancelled reservation" do
      @reservation.update!(status: "cancelled")

      get "/public/reservations/#{@reservation.confirmation_number}/cancelled", params: {
        hotel_id: @hotel.id,
        email: @guest.email
      }
      assert_response :success
    end

    test "cancelled redirects if reservation not cancelled" do
      # Reservation is confirmed, not cancelled
      get "/public/reservations/#{@reservation.confirmation_number}/cancelled", params: {
        hotel_id: @hotel.id,
        email: @guest.email
      }
      assert_redirected_to "/hotels/#{@hotel.subdomain}/reservations/lookup"
    end

    # ===========================================
    # Multi-tenancy Tests
    # ===========================================

    test "cannot access reservation from another account" do
      other_account = create(:account)
      other_reservation = nil

      ActsAsTenant.with_tenant(other_account) do
        other_hotel = create(:hotel, account: other_account)
        other_room_type = create(:room_type, hotel: other_hotel, account: other_account)
        other_guest = create(:guest, account: other_account, email: "other@example.com")
        other_reservation = create(:reservation,
          account: other_account,
          hotel: other_hotel,
          guest: other_guest,
          room_type: other_room_type,
          status: "confirmed",
          check_in_date: Date.current + 5.days,
          check_out_date: Date.current + 8.days
        )
      end

      # Try to access other account's reservation with our guest's email
      get "/public/reservations/#{other_reservation.confirmation_number}", params: {
        hotel_id: @hotel.id,
        email: @guest.email  # Wrong email for this reservation
      }
      assert_redirected_to "/hotels/#{@hotel.subdomain}/reservations/lookup"
    end

    # ===========================================
    # Business Rules Tests
    # ===========================================

    test "can_modify returns false for checked_in reservation" do
      room = create(:room, hotel: @hotel, account: @account, room_type: @room_type)
      checked_in = create(:reservation, :checked_in,
        account: @account,
        hotel: @hotel,
        guest: @guest,
        room_type: @room_type,
        room: room
      )
      assert_not checked_in.can_modify?
    end

    test "can_modify returns false within 24 hours of check-in" do
      near = create(:reservation,
        account: @account,
        hotel: @hotel,
        guest: @guest,
        room_type: @room_type,
        status: "confirmed",
        check_in_date: Date.current + 1.day,
        check_out_date: Date.current + 3.days
      )
      assert_not near.can_modify?
    end

    test "can_modify returns true for future reservation" do
      assert @reservation.can_modify?
    end

    test "can_cancel returns false for checked_in reservation" do
      room = create(:room, hotel: @hotel, account: @account, room_type: @room_type)
      checked_in = create(:reservation, :checked_in,
        account: @account,
        hotel: @hotel,
        guest: @guest,
        room_type: @room_type,
        room: room
      )
      assert_not checked_in.can_cancel?
    end

    test "can_cancel returns false for past check-in date" do
      past = create(:reservation,
        account: @account,
        hotel: @hotel,
        guest: @guest,
        room_type: @room_type,
        status: "confirmed",
        check_in_date: Date.current - 1.day,
        check_out_date: Date.current + 1.day
      )
      assert_not past.can_cancel?
    end

    test "can_cancel returns true for future reservation" do
      assert @reservation.can_cancel?
    end
  end
end
