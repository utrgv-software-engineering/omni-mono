# frozen_string_literal: true

require "application_system_test_case"

class GuestManagementTest < ApplicationSystemTestCase
  setup do
    @context = sign_in_as_role(:front_desk)
    @account = @context[:account]
  end

  test "staff can create a new guest" do
    visit new_guest_path

    # Use label text instead of field ID (Rails form helper generates label associations)
    fill_in "First name", with: "Jane"
    fill_in "Last name", with: "Smith"
    fill_in "Email", with: "jane.smith@example.com"
    fill_in "Phone", with: "555-9876"

    click_button "Create Guest"

    assert_text "Guest was successfully created"
    assert_text "Jane Smith"
  end

  test "staff can search for guests" do
    create(:guest, account: @account, first_name: "John", last_name: "Doe", email: "john@example.com")
    create(:guest, account: @account, first_name: "Jane", last_name: "Smith", email: "jane@example.com")

    visit guests_path

    fill_in "search", with: "John"
    click_button "Search"

    assert_text "John Doe"
    assert_no_text "Jane Smith"
  end
end
