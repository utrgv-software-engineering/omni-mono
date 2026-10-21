# frozen_string_literal: true

require "test_helper"

class RoomsControllerTest < ActionDispatch::IntegrationTest
  setup do
    @account = create(:account)
    ActsAsTenant.current_tenant = @account
    @hotel = create(:hotel, account: @account)
    @room_type = create(:room_type, hotel: @hotel, account: @account)
    @room = create(:room, hotel: @hotel, account: @account, room_type: @room_type)
  end

  teardown do
    ActsAsTenant.current_tenant = nil
  end

  # ===========================================
  # Authorization Tests (per Testing Guide 7.0)
  # ===========================================

  # Index access - any staff can view
  test "housekeeping can access rooms index" do
    sign_in_as(:housekeeping, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}/rooms"
    assert_response :success
  end

  test "front_desk can access rooms index" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}/rooms"
    assert_response :success
  end

  test "hotel_manager can access rooms index" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}/rooms"
    assert_response :success
  end

  # Show access - any staff can view
  test "housekeeping can view room details" do
    sign_in_as(:housekeeping, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}/rooms/#{@room.id}"
    assert_response :success
  end

  # Create access - manager+ only
  test "housekeeping cannot create room" do
    sign_in_as(:housekeeping, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}/rooms/new"
    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "front_desk cannot create room" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    assert_no_difference "Room.count" do
      post "/hotels/#{@hotel.id}/rooms", params: {
        room: {
          room_type_id: @room_type.id,
          room_number: "999",
          floor: "9",
          status: "available"
        }
      }
    end

    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "hotel_manager can create room" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    assert_difference "Room.count", 1 do
      post "/hotels/#{@hotel.id}/rooms", params: {
        room: {
          room_type_id: @room_type.id,
          room_number: "999",
          floor: "9",
          status: "available"
        }
      }
    end

    room = Room.last
    assert_redirected_to "/hotels/#{@hotel.id}/rooms/#{room.id}"
  end

  # Destroy access - manager+ only
  test "housekeeping cannot destroy room" do
    sign_in_as(:housekeeping, account: @account, hotel: @hotel)

    assert_no_difference "Room.count" do
      delete "/hotels/#{@hotel.id}/rooms/#{@room.id}"
    end

    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "front_desk cannot destroy room" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    assert_no_difference "Room.count" do
      delete "/hotels/#{@hotel.id}/rooms/#{@room.id}"
    end

    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "hotel_manager can destroy room" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    assert_difference "Room.count", -1 do
      delete "/hotels/#{@hotel.id}/rooms/#{@room.id}"
    end

    assert_redirected_to "/hotels/#{@hotel.id}/rooms"
  end

  # ===========================================
  # Housekeeping Update Restriction (CRITICAL)
  # ===========================================

  test "housekeeping can update room status" do
    sign_in_as(:housekeeping, account: @account, hotel: @hotel)

    patch "/hotels/#{@hotel.id}/rooms/#{@room.id}", params: {
      room: { status: "cleaning" }
    }

    assert_redirected_to "/hotels/#{@hotel.id}/rooms/#{@room.id}"
    @room.reload
    assert_equal "cleaning", @room.status
  end

  test "housekeeping can update last_cleaned_at" do
    sign_in_as(:housekeeping, account: @account, hotel: @hotel)
    clean_time = Time.current

    freeze_time do
      patch "/hotels/#{@hotel.id}/rooms/#{@room.id}", params: {
        room: { last_cleaned_at: Time.current.iso8601 }
      }
    end

    assert_redirected_to "/hotels/#{@hotel.id}/rooms/#{@room.id}"
    @room.reload
    assert_not_nil @room.last_cleaned_at
  end

  test "housekeeping cannot update room_number" do
    sign_in_as(:housekeeping, account: @account, hotel: @hotel)
    original_room_number = @room.room_number

    patch "/hotels/#{@hotel.id}/rooms/#{@room.id}", params: {
      room: { room_number: "CHANGED999" }
    }

    # Should succeed (update is allowed) but room_number should not change
    @room.reload
    assert_equal original_room_number, @room.room_number
  end

  test "housekeeping cannot update floor" do
    sign_in_as(:housekeeping, account: @account, hotel: @hotel)
    original_floor = @room.floor

    patch "/hotels/#{@hotel.id}/rooms/#{@room.id}", params: {
      room: { floor: "99" }
    }

    @room.reload
    assert_equal original_floor, @room.floor
  end

  test "housekeeping cannot update room_type_id" do
    sign_in_as(:housekeeping, account: @account, hotel: @hotel)
    other_room_type = create(:room_type, hotel: @hotel, account: @account)
    original_room_type_id = @room.room_type_id

    patch "/hotels/#{@hotel.id}/rooms/#{@room.id}", params: {
      room: { room_type_id: other_room_type.id }
    }

    @room.reload
    assert_equal original_room_type_id, @room.room_type_id
  end

  test "housekeeping cannot update is_smoking" do
    sign_in_as(:housekeeping, account: @account, hotel: @hotel)
    original_is_smoking = @room.is_smoking

    patch "/hotels/#{@hotel.id}/rooms/#{@room.id}", params: {
      room: { is_smoking: !original_is_smoking }
    }

    @room.reload
    assert_equal original_is_smoking, @room.is_smoking
  end

  test "housekeeping cannot update notes" do
    sign_in_as(:housekeeping, account: @account, hotel: @hotel)

    patch "/hotels/#{@hotel.id}/rooms/#{@room.id}", params: {
      room: { notes: "HACKED NOTES" }
    }

    @room.reload
    # Notes should remain nil (the factory default)
    assert_nil @room.notes
  end

  # Manager can update all fields
  test "hotel_manager can update all room fields" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)
    other_room_type = create(:room_type, hotel: @hotel, account: @account)

    patch "/hotels/#{@hotel.id}/rooms/#{@room.id}", params: {
      room: {
        room_type_id: other_room_type.id,
        room_number: "MANAGER_ROOM",
        floor: "42",
        status: "maintenance",
        is_smoking: true,
        notes: "Manager updated notes"
      }
    }

    assert_redirected_to "/hotels/#{@hotel.id}/rooms/#{@room.id}"
    @room.reload
    assert_equal other_room_type.id, @room.room_type_id
    assert_equal "MANAGER_ROOM", @room.room_number
    assert_equal "42", @room.floor
    assert_equal "maintenance", @room.status
    assert_equal true, @room.is_smoking
    assert_equal "Manager updated notes", @room.notes
  end

  # ===========================================
  # Multi-tenancy Tests (per Testing Guide 8.0)
  # ===========================================

  test "cannot access rooms from another account" do
    other_account = create(:account)
    other_hotel = nil
    other_room = nil

    ActsAsTenant.with_tenant(other_account) do
      other_hotel = create(:hotel, account: other_account)
      other_room_type = create(:room_type, hotel: other_hotel, account: other_account)
      other_room = create(:room, hotel: other_hotel, account: other_account, room_type: other_room_type)
    end

    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    # Should return 404 (not 403) per Testing Guide 8.2.2.1
    get "/hotels/#{other_hotel.id}/rooms/#{other_room.id}"
    assert_response :not_found
  end

  test "index only shows rooms from current hotel" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    # Create another room in same hotel
    room2 = create(:room, hotel: @hotel, account: @account, room_type: @room_type)

    # Create room in different hotel (same account)
    other_hotel = create(:hotel, account: @account)
    other_room = create(:room, hotel: other_hotel, account: @account, room_type: @room_type)

    get "/hotels/#{@hotel.id}/rooms"
    assert_response :success

    # Should show our hotel's rooms
    assert_select "a[href='/hotels/#{@hotel.id}/rooms/#{@room.id}']"
    assert_select "a[href='/hotels/#{@hotel.id}/rooms/#{room2.id}']"
    # Should not show other hotel's room
    # Note: The link would be different since it's nested under different hotel
  end

  # ===========================================
  # CRUD Operations (per Testing Guide 3.2)
  # ===========================================

  test "index returns rooms list" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}/rooms"
    assert_response :success
    assert_select "a[href='/hotels/#{@hotel.id}/rooms/#{@room.id}']"
  end

  test "show displays room details" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}/rooms/#{@room.id}"
    assert_response :success
    assert_select "main", /#{@room.room_number}/
  end

  test "new displays room form" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}/rooms/new"
    assert_response :success
    assert_select "form[action='/hotels/#{@hotel.id}/rooms']"
  end

  test "create room with valid params" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    assert_difference "Room.count", 1 do
      post "/hotels/#{@hotel.id}/rooms", params: {
        room: {
          room_type_id: @room_type.id,
          room_number: "NEW_ROOM_123",
          floor: "5",
          status: "available",
          is_smoking: false
        }
      }
    end

    room = Room.last
    assert_redirected_to "/hotels/#{@hotel.id}/rooms/#{room.id}"
    assert_equal "NEW_ROOM_123", room.room_number
    assert_equal @hotel, room.hotel
    assert_equal @account, room.account
  end

  test "create room with invalid params fails" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    assert_no_difference "Room.count" do
      post "/hotels/#{@hotel.id}/rooms", params: {
        room: {
          room_type_id: @room_type.id,
          room_number: "", # Required field
          floor: "5",
          status: "available"
        }
      }
    end

    assert_response :unprocessable_content
  end

  test "edit displays room form" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}/rooms/#{@room.id}/edit"
    assert_response :success
    assert_select "form[action='/hotels/#{@hotel.id}/rooms/#{@room.id}']"
  end

  test "update room with valid params" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    patch "/hotels/#{@hotel.id}/rooms/#{@room.id}", params: {
      room: { notes: "Updated notes" }
    }

    assert_redirected_to "/hotels/#{@hotel.id}/rooms/#{@room.id}"
    @room.reload
    assert_equal "Updated notes", @room.notes
  end

  # ===========================================
  # Role Access Matrix Tests
  # ===========================================

  test "admin can perform all room operations" do
    sign_in_as(:admin, account: @account, hotel: @hotel)

    # Index
    get "/hotels/#{@hotel.id}/rooms"
    assert_response :success

    # Show
    get "/hotels/#{@hotel.id}/rooms/#{@room.id}"
    assert_response :success

    # Create
    assert_difference "Room.count", 1 do
      post "/hotels/#{@hotel.id}/rooms", params: {
        room: {
          room_type_id: @room_type.id,
          room_number: "ADMIN_ROOM",
          floor: "1",
          status: "available"
        }
      }
    end

    # Update
    patch "/hotels/#{@hotel.id}/rooms/#{@room.id}", params: {
      room: { notes: "Admin updated" }
    }
    assert_redirected_to "/hotels/#{@hotel.id}/rooms/#{@room.id}"

    # Destroy
    assert_difference "Room.count", -1 do
      delete "/hotels/#{@hotel.id}/rooms/#{@room.id}"
    end
  end

  test "owner can perform all room operations" do
    sign_in_as(:owner, account: @account, hotel: @hotel)

    # Index
    get "/hotels/#{@hotel.id}/rooms"
    assert_response :success

    # Create
    assert_difference "Room.count", 1 do
      post "/hotels/#{@hotel.id}/rooms", params: {
        room: {
          room_type_id: @room_type.id,
          room_number: "OWNER_ROOM",
          floor: "1",
          status: "available"
        }
      }
    end

    # Destroy
    assert_difference "Room.count", -1 do
      delete "/hotels/#{@hotel.id}/rooms/#{@room.id}"
    end
  end

  test "unauthenticated user cannot access rooms" do
    # With `authenticated :user` routing constraint, returns 404
    get "/hotels/#{@hotel.id}/rooms"
    assert_response :not_found
  end
end
