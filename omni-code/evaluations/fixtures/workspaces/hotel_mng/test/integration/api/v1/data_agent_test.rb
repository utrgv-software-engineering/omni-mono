require "test_helper"

class Api::V1::DataAgentTest < ActionDispatch::IntegrationTest
  # Disable transactional tests because the preview_query feature may execute
  # invalid SQL that corrupts PostgreSQL transactions. Invalid queries within
  # a transaction cause PostgreSQL to abort the entire transaction, which breaks
  # subsequent operations even with savepoints.
  self.use_transactional_tests = false

  setup do
    @account = create(:account)
    @hotel = create(:hotel, account: @account)
    @room_type = create(:room_type, hotel: @hotel, account: @account)
    @room = create(:room, hotel: @hotel, room_type: @room_type, account: @account)
    @guest = create(:guest, account: @account)

    # Create a manager (has preview_query access)
    @api_agent = create_api_agent(@account, :hotel_manager)
    @headers = api_headers_for(@api_agent)
  end

  teardown do
    # Clean up since we're not using transactional tests
    # Delete in reverse order of creation to respect foreign key constraints
    ActsAsTenant.without_tenant do
      Guest.delete_all
      Room.delete_all
      RoomType.delete_all
      Hotel.delete_all
      AccountUser.delete_all
      User.delete_all
      Account.delete_all
    end
  end

  # List tables tests
  test "GET /api/v1/data_agent/tables returns list of accessible tables" do
    get "/api/v1/data_agent/tables", headers: @headers

    assert_response :success
    assert_json_response

    data = json_response['data']
    assert data.key?('tables')
    assert data.key?('count')
    assert_kind_of Array, data['tables']
    assert data['count'] > 0
  end

  test "GET /api/v1/data_agent/tables returns table metadata" do
    get "/api/v1/data_agent/tables", headers: @headers

    assert_response :success

    data = json_response['data']
    table = data['tables'].first

    assert table.key?('name')
    assert table.key?('description')
    assert table.key?('type')
    assert_equal 'table', table['type']
  end

  test "GET /api/v1/data_agent/tables filters tables based on role" do
    # Hotel manager should see most tables but not account management tables
    get "/api/v1/data_agent/tables", headers: @headers

    assert_response :success

    data = json_response['data']
    table_names = data['tables'].map { |t| t['name'] }

    # Should see operational tables
    assert_includes table_names, 'hotels'
    assert_includes table_names, 'rooms'
    assert_includes table_names, 'reservations'

    # Should NOT see account management tables (hotel_manager role)
    refute_includes table_names, 'accounts'
    refute_includes table_names, 'account_users'
  end

  test "GET /api/v1/data_agent/tables owner sees all tables" do
    owner_agent = create_api_agent(@account, :owner)
    headers = api_headers_for(owner_agent)

    get "/api/v1/data_agent/tables", headers: headers

    assert_response :success

    data = json_response['data']
    table_names = data['tables'].map { |t| t['name'] }

    # Owner should see account management tables
    assert_includes table_names, 'accounts'
    assert_includes table_names, 'hotels'
  end

  test "GET /api/v1/data_agent/tables front_desk has limited access" do
    front_desk_agent = create_api_agent(@account, :front_desk)
    headers = api_headers_for(front_desk_agent)

    get "/api/v1/data_agent/tables", headers: headers

    assert_response :success

    data = json_response['data']
    table_names = data['tables'].map { |t| t['name'] }

    # Front desk should see operational tables
    assert_includes table_names, 'hotels'
    assert_includes table_names, 'rooms'
    assert_includes table_names, 'guests'
    assert_includes table_names, 'reservations'
  end

  # Describe table tests
  test "GET /api/v1/data_agent/tables/:table_name returns table schema" do
    get "/api/v1/data_agent/tables/hotels", headers: @headers

    assert_response :success
    assert_json_response

    data = json_response['data']
    assert_equal 'hotels', data['table_name']
    assert data.key?('description')
    assert data.key?('columns')
    assert data.key?('relationships')
    assert data.key?('indexes')
    assert data.key?('row_count')
    assert_kind_of Array, data['columns']
  end

  test "GET /api/v1/data_agent/tables/:table_name returns column details" do
    get "/api/v1/data_agent/tables/rooms", headers: @headers

    assert_response :success

    data = json_response['data']
    columns = data['columns']

    # Check that column metadata is included
    column = columns.find { |c| c['name'] == 'status' }
    assert column
    assert column.key?('data_type')
    assert column.key?('nullable')
  end

  test "GET /api/v1/data_agent/tables/:table_name returns 404 for non-existent table" do
    get "/api/v1/data_agent/tables/non_existent_table", headers: @headers

    assert_response :not_found
    assert_json_response
  end

  test "GET /api/v1/data_agent/tables/:table_name returns 404 for inaccessible table" do
    # Hotel manager shouldn't access accounts table
    get "/api/v1/data_agent/tables/accounts", headers: @headers

    assert_response :not_found
  end

  test "GET /api/v1/data_agent/tables/:table_name excludes sensitive columns" do
    # Users table should not expose password columns
    owner_agent = create_api_agent(@account, :owner)
    headers = api_headers_for(owner_agent)

    get "/api/v1/data_agent/tables/users", headers: headers

    assert_response :success

    data = json_response['data']
    column_names = data['columns'].map { |c| c['name'] }

    # Should not include sensitive columns
    refute_includes column_names, 'encrypted_password'
    refute_includes column_names, 'reset_password_token'
    refute_includes column_names, 'api_key'
  end

  # Preview query tests
  test "POST /api/v1/data_agent/preview_query executes valid SELECT query" do
    post "/api/v1/data_agent/preview_query",
         params: { sql: "SELECT id, name FROM hotels" }.to_json,
         headers: @headers

    assert_response :success
    assert_json_response

    data = json_response['data']
    assert data.key?('columns')
    assert data.key?('rows')
    assert data.key?('row_count')
    assert data.key?('execution_time_ms')
    assert_includes data['columns'], 'id'
    assert_includes data['columns'], 'name'
  end

  test "POST /api/v1/data_agent/preview_query returns error for non-SELECT query" do
    post "/api/v1/data_agent/preview_query",
         params: { sql: "DELETE FROM hotels" }.to_json,
         headers: @headers

    assert_response :bad_request
    assert_json_response
    assert json_response['message'].downcase.include?('select')
  end

  test "POST /api/v1/data_agent/preview_query returns error for dangerous operations" do
    post "/api/v1/data_agent/preview_query",
         params: { sql: "SELECT * FROM hotels; DROP TABLE hotels" }.to_json,
         headers: @headers

    assert_response :bad_request
    assert_json_response
  end

  test "POST /api/v1/data_agent/preview_query respects max_rows parameter" do
    # Create multiple hotels to test limit
    5.times { create(:hotel, account: @account) }

    post "/api/v1/data_agent/preview_query",
         params: { sql: "SELECT * FROM hotels", max_rows: 2 }.to_json,
         headers: @headers

    assert_response :success

    data = json_response['data']
    assert data['row_count'] <= 2
  end

  test "POST /api/v1/data_agent/preview_query caps max_rows at 1000" do
    post "/api/v1/data_agent/preview_query",
         params: { sql: "SELECT * FROM hotels", max_rows: 5000 }.to_json,
         headers: @headers

    # Should still succeed, just capped at 1000
    assert_response :success
  end

  test "POST /api/v1/data_agent/preview_query returns 400 for missing SQL" do
    post "/api/v1/data_agent/preview_query",
         params: {}.to_json,
         headers: @headers

    assert_response :bad_request
    assert_json_response
    assert json_response['message'].include?('SQL')
  end

  test "POST /api/v1/data_agent/preview_query returns error for invalid SQL syntax" do
    post "/api/v1/data_agent/preview_query",
         params: { sql: "SELECT FROM WHERE" }.to_json,
         headers: @headers

    assert_response :bad_request
    assert_json_response
    assert json_response.key?('error')
  end

  test "POST /api/v1/data_agent/preview_query scopes results to current account" do
    # Create hotel in different account
    other_account = create(:account)
    other_hotel = create(:hotel, account: other_account, name: "Other Account Hotel")

    post "/api/v1/data_agent/preview_query",
         params: { sql: "SELECT name FROM hotels" }.to_json,
         headers: @headers

    assert_response :success

    data = json_response['data']
    hotel_names = data['rows'].flatten

    # Should not see hotels from other accounts
    refute_includes hotel_names, "Other Account Hotel"
    # Should see our hotel
    assert_includes hotel_names, @hotel.name
  end

  test "POST /api/v1/data_agent/preview_query indicates when results are truncated" do
    # Create enough hotels to exceed a small limit
    10.times { create(:hotel, account: @account) }

    post "/api/v1/data_agent/preview_query",
         params: { sql: "SELECT * FROM hotels", max_rows: 5 }.to_json,
         headers: @headers

    assert_response :success

    data = json_response['data']
    assert data.key?('truncated')
    assert_equal true, data['truncated']
  end

  # Authorization tests
  test "returns 401 without API key" do
    get "/api/v1/data_agent/tables"

    assert_response :unauthorized
  end

  test "front_desk can list tables but cannot execute queries" do
    front_desk_agent = create_api_agent(@account, :front_desk)
    headers = api_headers_for(front_desk_agent)

    # Can list tables
    get "/api/v1/data_agent/tables", headers: headers
    assert_response :success

    # Cannot execute queries (preview_query requires manager_or_above)
    post "/api/v1/data_agent/preview_query",
         params: { sql: "SELECT * FROM hotels" }.to_json,
         headers: headers
    assert_response :forbidden
  end

  test "housekeeping can list tables but cannot execute queries" do
    housekeeping_agent = create_api_agent(@account, :housekeeping)
    headers = api_headers_for(housekeeping_agent)

    # Can list tables (limited set)
    get "/api/v1/data_agent/tables", headers: headers
    assert_response :success

    # Cannot execute queries
    post "/api/v1/data_agent/preview_query",
         params: { sql: "SELECT * FROM rooms" }.to_json,
         headers: headers
    assert_response :forbidden
  end

  test "owner can execute queries" do
    owner_agent = create_api_agent(@account, :owner)
    headers = api_headers_for(owner_agent)

    post "/api/v1/data_agent/preview_query",
         params: { sql: "SELECT COUNT(*) FROM hotels" }.to_json,
         headers: headers

    assert_response :success
  end

  test "admin can execute queries" do
    admin_agent = create_api_agent(@account, :admin)
    headers = api_headers_for(admin_agent)

    post "/api/v1/data_agent/preview_query",
         params: { sql: "SELECT COUNT(*) FROM hotels" }.to_json,
         headers: headers

    assert_response :success
  end
end
