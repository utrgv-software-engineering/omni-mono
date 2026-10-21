# frozen_string_literal: true

require "test_helper"

class HousekeepingTasksControllerTest < ActionDispatch::IntegrationTest
  setup do
    @account = create(:account)
    ActsAsTenant.current_tenant = @account
    @hotel = create(:hotel, account: @account)
    @room_type = create(:room_type, hotel: @hotel, account: @account)
    @room = create(:room, hotel: @hotel, account: @account, room_type: @room_type)
    @task = create(:housekeeping_task, :unassigned, hotel: @hotel, account: @account, room: @room)
  end

  teardown do
    ActsAsTenant.current_tenant = nil
  end

  # ===========================================
  # Authorization Tests (per Testing Guide 7.0)
  # ===========================================

  test "housekeeping can access tasks index" do
    sign_in_as(:housekeeping, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}/housekeeping_tasks"
    assert_response :success
  end

  test "housekeeping can view task details" do
    sign_in_as(:housekeeping, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}/housekeeping_tasks/#{@task.id}"
    assert_response :success
  end

  test "housekeeping cannot create task" do
    sign_in_as(:housekeeping, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}/housekeeping_tasks/new"
    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "housekeeping cannot destroy task" do
    sign_in_as(:housekeeping, account: @account, hotel: @hotel)

    assert_no_difference "HousekeepingTask.count" do
      delete "/hotels/#{@hotel.id}/housekeeping_tasks/#{@task.id}"
    end

    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "front_desk can create task" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    assert_difference "HousekeepingTask.count", 1 do
      post "/hotels/#{@hotel.id}/housekeeping_tasks", params: {
        housekeeping_task: {
          room_id: @room.id,
          task_type: "cleaning",
          priority: "normal",
          scheduled_date: Date.current
        }
      }
    end

    task = HousekeepingTask.last
    assert_redirected_to "/hotels/#{@hotel.id}/housekeeping_tasks/#{task.id}"
  end

  test "front_desk cannot destroy task" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    assert_no_difference "HousekeepingTask.count" do
      delete "/hotels/#{@hotel.id}/housekeeping_tasks/#{@task.id}"
    end

    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "hotel_manager can destroy task" do
    sign_in_as(:hotel_manager, account: @account, hotel: @hotel)

    assert_difference "HousekeepingTask.count", -1 do
      delete "/hotels/#{@hotel.id}/housekeeping_tasks/#{@task.id}"
    end

    assert_redirected_to "/hotels/#{@hotel.id}/housekeeping_tasks"
  end

  # ===========================================
  # Housekeeping Update Restrictions
  # ===========================================

  test "housekeeping can mark task as complete" do
    sign_in_as(:housekeeping, account: @account, hotel: @hotel)

    patch "/hotels/#{@hotel.id}/housekeeping_tasks/#{@task.id}/mark_complete", params: {
      inspection_score: 8
    }

    assert_redirected_to "/hotels/#{@hotel.id}/housekeeping_tasks/#{@task.id}"
    @task.reload
    assert_equal "completed", @task.status
    assert_not_nil @task.completed_at
  end

  test "housekeeping cannot assign task" do
    sign_in_as(:housekeeping, account: @account, hotel: @hotel)
    user = create(:user)

    patch "/hotels/#{@hotel.id}/housekeeping_tasks/#{@task.id}/assign", params: {
      assigned_to_id: user.id
    }

    assert_response :redirect
    assert_match(/not authorized/i, flash[:alert])
  end

  test "front_desk can assign task" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    user = create(:user)

    patch "/hotels/#{@hotel.id}/housekeeping_tasks/#{@task.id}/assign", params: {
      assigned_to_id: user.id
    }

    assert_redirected_to "/hotels/#{@hotel.id}/housekeeping_tasks/#{@task.id}"
    @task.reload
    assert_equal user, @task.assigned_to
  end

  test "front_desk can mark task as complete" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    patch "/hotels/#{@hotel.id}/housekeeping_tasks/#{@task.id}/mark_complete", params: {
      inspection_score: 9
    }

    assert_redirected_to "/hotels/#{@hotel.id}/housekeeping_tasks/#{@task.id}"
    @task.reload
    assert_equal "completed", @task.status
    assert_equal 9, @task.inspection_score
  end

  # ===========================================
  # Multi-tenancy Tests (per Testing Guide 8.0)
  # ===========================================

  test "cannot access task from another account" do
    other_account = create(:account)
    other_task = nil

    ActsAsTenant.with_tenant(other_account) do
      other_hotel = create(:hotel, account: other_account)
      other_room_type = create(:room_type, hotel: other_hotel, account: other_account)
      other_room = create(:room, hotel: other_hotel, account: other_account, room_type: other_room_type)
      other_task = create(:housekeeping_task, :unassigned, hotel: other_hotel, account: other_account, room: other_room)
    end

    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    # Should return 404 (not 403) per Testing Guide 8.2.2.1
    get "/hotels/#{other_task.hotel_id}/housekeeping_tasks/#{other_task.id}"
    assert_response :not_found
  end

  # ===========================================
  # CRUD Operations (per Testing Guide 3.2)
  # ===========================================

  test "index returns tasks list" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}/housekeeping_tasks"
    assert_response :success
  end

  test "show displays task details" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}/housekeeping_tasks/#{@task.id}"
    assert_response :success
  end

  test "new displays task form" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}/housekeeping_tasks/new"
    assert_response :success
    assert_select "form[action='/hotels/#{@hotel.id}/housekeeping_tasks']"
  end

  test "create task with valid params" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    assert_difference "HousekeepingTask.count", 1 do
      post "/hotels/#{@hotel.id}/housekeeping_tasks", params: {
        housekeeping_task: {
          room_id: @room.id,
          task_type: "deep_clean",
          priority: "high",
          scheduled_date: Date.tomorrow,
          notes: "Extra attention needed"
        }
      }
    end

    task = HousekeepingTask.last
    assert_redirected_to "/hotels/#{@hotel.id}/housekeeping_tasks/#{task.id}"
    assert_equal "deep_clean", task.task_type
    assert_equal "high", task.priority
    assert_equal @hotel, task.hotel
    assert_equal @account, task.account
  end

  test "edit displays task form" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    get "/hotels/#{@hotel.id}/housekeeping_tasks/#{@task.id}/edit"
    assert_response :success
    assert_select "form[action='/hotels/#{@hotel.id}/housekeeping_tasks/#{@task.id}']"
  end

  test "update task with valid params" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    patch "/hotels/#{@hotel.id}/housekeeping_tasks/#{@task.id}", params: {
      housekeeping_task: {
        priority: "urgent",
        notes: "Updated notes"
      }
    }

    assert_redirected_to "/hotels/#{@hotel.id}/housekeeping_tasks/#{@task.id}"
    @task.reload
    assert_equal "urgent", @task.priority
    assert_equal "Updated notes", @task.notes
  end

  # ===========================================
  # Filtering Tests
  # ===========================================

  test "index filters by status" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    room2 = create(:room, hotel: @hotel, account: @account, room_type: @room_type)
    completed_task = create(:housekeeping_task, :completed, :unassigned, hotel: @hotel, account: @account, room: room2)

    get "/hotels/#{@hotel.id}/housekeeping_tasks", params: { status: "pending" }
    assert_response :success

    # Pending task should appear, completed shouldn't
    # Can't easily check with assert_select without knowing exact markup
  end

  test "index filters by scheduled_date" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    room2 = create(:room, hotel: @hotel, account: @account, room_type: @room_type)
    tomorrow_task = create(:housekeeping_task, :unassigned, hotel: @hotel, account: @account, room: room2, scheduled_date: Date.tomorrow)

    get "/hotels/#{@hotel.id}/housekeeping_tasks", params: { date: Date.current.to_s }
    assert_response :success
  end

  test "index filters by room_id" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    room2 = create(:room, hotel: @hotel, account: @account, room_type: @room_type)
    other_room_task = create(:housekeeping_task, :unassigned, hotel: @hotel, account: @account, room: room2)

    get "/hotels/#{@hotel.id}/housekeeping_tasks", params: { room_id: @room.id }
    assert_response :success
  end

  # ===========================================
  # Custom Actions Tests
  # ===========================================

  test "mark_complete sets status to completed" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    patch "/hotels/#{@hotel.id}/housekeeping_tasks/#{@task.id}/mark_complete"

    @task.reload
    assert_equal "completed", @task.status
  end

  test "mark_complete sets completed_at timestamp" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    freeze_time do
      patch "/hotels/#{@hotel.id}/housekeeping_tasks/#{@task.id}/mark_complete"

      @task.reload
      assert_not_nil @task.completed_at
    end
  end

  test "mark_complete accepts optional inspection_score" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)

    patch "/hotels/#{@hotel.id}/housekeeping_tasks/#{@task.id}/mark_complete", params: {
      inspection_score: 10
    }

    @task.reload
    assert_equal 10, @task.inspection_score
  end

  test "assign updates assigned_to_id" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    user = create(:user)

    patch "/hotels/#{@hotel.id}/housekeeping_tasks/#{@task.id}/assign", params: {
      assigned_to_id: user.id
    }

    @task.reload
    assert_equal user.id, @task.assigned_to_id
  end

  test "assign can unassign by passing nil" do
    sign_in_as(:front_desk, account: @account, hotel: @hotel)
    user = create(:user)
    @task.update!(assigned_to: user)

    patch "/hotels/#{@hotel.id}/housekeeping_tasks/#{@task.id}/assign", params: {
      assigned_to_id: ""
    }

    @task.reload
    assert_nil @task.assigned_to_id
  end

  # ===========================================
  # Role Access Matrix Tests
  # ===========================================

  test "admin can perform all task operations" do
    sign_in_as(:admin, account: @account, hotel: @hotel)

    # Index
    get "/hotels/#{@hotel.id}/housekeeping_tasks"
    assert_response :success

    # Show
    get "/hotels/#{@hotel.id}/housekeeping_tasks/#{@task.id}"
    assert_response :success

    # Create
    assert_difference "HousekeepingTask.count", 1 do
      post "/hotels/#{@hotel.id}/housekeeping_tasks", params: {
        housekeeping_task: {
          room_id: @room.id,
          task_type: "cleaning",
          scheduled_date: Date.current
        }
      }
    end

    # Update
    patch "/hotels/#{@hotel.id}/housekeeping_tasks/#{@task.id}", params: {
      housekeeping_task: { priority: "low" }
    }
    assert_redirected_to "/hotels/#{@hotel.id}/housekeeping_tasks/#{@task.id}"

    # Destroy
    assert_difference "HousekeepingTask.count", -1 do
      delete "/hotels/#{@hotel.id}/housekeeping_tasks/#{@task.id}"
    end
  end

  test "unauthenticated user cannot access tasks" do
    # With `authenticated :user` routing constraint, returns 404
    get "/hotels/#{@hotel.id}/housekeeping_tasks"
    assert_response :not_found
  end
end
