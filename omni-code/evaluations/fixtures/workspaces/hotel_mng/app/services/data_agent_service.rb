class DataAgentService
  EXCLUDED_TABLES = %w[
    schema_migrations
    ar_internal_metadata
    solid_cable_messages
    solid_cache_entries
    solid_queue_blocked_executions
    solid_queue_claimed_executions
    solid_queue_failed_executions
    solid_queue_jobs
    solid_queue_pauses
    solid_queue_processes
    solid_queue_ready_executions
    solid_queue_recurring_executions
    solid_queue_recurring_tasks
    solid_queue_scheduled_executions
    solid_queue_semaphores
    active_storage_attachments
    active_storage_blobs
    active_storage_variant_records
    pay_charges
    pay_customers
    pay_merchants
    pay_payment_methods
    pay_subscriptions
    pay_webhooks
  ].freeze
  
  SENSITIVE_COLUMNS = %w[
    password
    encrypted_password
    password_digest
    token
    api_key
    secret
    authentication_token
    reset_password_token
    confirmation_token
    unlock_token
  ].freeze
  
  TABLE_DESCRIPTIONS = {
    'accounts' => 'Organization/tenant root - represents hotel companies or chains',
    'hotels' => 'Individual hotel properties within an account',
    'users' => 'System users who can access the platform',
    'account_users' => 'Links users to accounts with specific roles',
    'roles' => 'Available roles for authorization (via Rolify)',
    'account_users_roles' => 'Role assignments for account users',
    'rooms' => 'Individual hotel rooms',
    'room_types' => 'Room categories (e.g., Standard, Deluxe, Suite)',
    'guests' => 'Guest profiles and contact information',
    'reservations' => 'Room bookings and reservation details',
    'housekeeping_tasks' => 'Cleaning and maintenance tasks for rooms',
    'maintenance_requests' => 'Repair and maintenance requests',
    'amenities' => 'Available amenities (master list)',
    'hotel_amenities' => 'Amenities offered by specific hotels',
    'room_type_amenities' => 'Amenities included with room types',
    'services' => 'Additional services offered by hotels',
    'hotel_policies' => 'Hotel-specific policies and rules',
    'cancellation_policies' => 'Cancellation rules and fees',
    'charges' => 'Additional charges and fees',
    'reservation_charges' => 'Charges applied to reservations',
    'guest_preferences' => 'Guest preferences and special requests',
    'rate_plans' => 'Pricing plans for rooms',
    'seasonal_rates' => 'Date-based pricing adjustments'
  }.freeze
  
  attr_reader :account, :account_user
  
  def initialize(account, account_user)
    @account = account
    @account_user = account_user
  end
  
  def list_tables
    tables = fetch_accessible_tables
    
    {
      tables: tables.map { |table_name| table_info(table_name) },
      count: tables.length
    }
  end
  
  def describe_table(table_name)
    unless table_exists?(table_name)
      return { error: "Table '#{table_name}' does not exist or is not accessible" }
    end
    
    unless can_access_table?(table_name)
      return { error: "Access denied to table '#{table_name}'" }
    end
    
    {
      table_name: table_name,
      description: TABLE_DESCRIPTIONS[table_name] || "Table containing #{table_name.humanize.downcase} data",
      columns: fetch_columns(table_name),
      relationships: fetch_relationships(table_name),
      indexes: fetch_indexes(table_name),
      row_count: fetch_row_count(table_name),
      sample_queries: generate_sample_queries(table_name)
    }
  end
  
  def execute_query(sql, max_rows: 100)
    # Validate query is SELECT only
    normalized_sql = sql.strip.downcase
    unless normalized_sql.starts_with?('select')
      return { error: "Only SELECT queries are allowed" }
    end
    
    # Check for dangerous keywords
    dangerous_keywords = %w[insert update delete drop create alter truncate grant revoke]
    if dangerous_keywords.any? { |keyword| normalized_sql.include?(keyword) }
      return { error: "Query contains forbidden operations" }
    end
    
    # Add tenant scoping to query if needed
    scoped_sql = add_tenant_scoping(sql)

    # Execute with timeout, using a separate connection to avoid poisoning
    # the current transaction on error
    start_time = Time.current
    result = nil
    error_result = nil

    ActiveRecord::Base.connection_pool.with_connection do |conn|
      begin
        conn.transaction(requires_new: true) do
          # Set statement timeout (PostgreSQL specific)
          conn.execute("SET LOCAL statement_timeout = '30s'")

          # Execute query with limit
          limited_sql = add_limit_to_query(scoped_sql, max_rows + 1)
          result = conn.select_all(limited_sql)
        end
      rescue ActiveRecord::StatementTimeout
        error_result = { timeout: true }
      rescue ActiveRecord::StatementInvalid => e
        error_result = parse_sql_error(e.message, sql)
      rescue => e
        error_result = { error: "Query execution failed: #{e.message}" }
      end
    end

    return error_result if error_result

    execution_time_ms = ((Time.current - start_time) * 1000).round(2)

    rows = result.to_a
    truncated = rows.length > max_rows
    rows = rows.first(max_rows) if truncated

    {
      columns: result.columns,
      rows: rows.map(&:values),
      row_count: rows.length,
      truncated: truncated,
      execution_time_ms: execution_time_ms
    }
  end
  
  private
  
  def fetch_accessible_tables
    # Get all tables from database
    all_tables = ActiveRecord::Base.connection.tables
    
    # Filter out system tables
    user_tables = all_tables - EXCLUDED_TABLES
    
    # Filter based on user role
    if account_user_has_role?(:owner, :admin)
      user_tables
    elsif account_user_has_role?(:hotel_manager)
      # Hotel managers can see most tables except account management
      user_tables - %w[accounts account_users account_users_roles roles]
    elsif account_user_has_role?(:front_desk)
      # Front desk can see operational tables
      %w[hotels rooms room_types guests reservations housekeeping_tasks amenities services]
    else
      # Minimal access for other roles
      %w[hotels rooms room_types]
    end
  end
  
  def table_info(table_name)
    {
      name: table_name,
      type: 'table',
      description: TABLE_DESCRIPTIONS[table_name] || "Table containing #{table_name.humanize.downcase} data",
      row_count: fetch_row_count(table_name),
      primary_key: fetch_primary_key(table_name),
      created_at: nil, # Could fetch from information_schema if needed
      updated_at: fetch_last_updated(table_name)
    }
  end
  
  def fetch_columns(table_name)
    columns = ActiveRecord::Base.connection.columns(table_name)
    
    columns.map do |column|
      # Skip sensitive columns
      next if SENSITIVE_COLUMNS.include?(column.name)
      
      {
        name: column.name,
        data_type: column.sql_type,
        nullable: column.null,
        default_value: column.default,
        is_primary_key: column.name == fetch_primary_key(table_name),
        is_foreign_key: column.name.ends_with?('_id'),
        foreign_key_table: infer_foreign_table(column.name),
        foreign_key_column: 'id',
        description: column_description(table_name, column.name),
        enum_values: fetch_enum_values(table_name, column.name)
      }
    end.compact
  end
  
  def fetch_relationships(table_name)
    relationships = []
    
    # Try to get model class
    model_class = table_name.classify.safe_constantize
    return relationships unless model_class
    
    # Get associations from ActiveRecord
    if model_class.respond_to?(:reflect_on_all_associations)
      model_class.reflect_on_all_associations.each do |assoc|
        next if assoc.polymorphic?
        
        relationship_type = case assoc.macro
        when :belongs_to then 'belongs_to'
        when :has_many then 'has_many'
        when :has_one then 'has_one'
        else next
        end
        
        relationships << {
          type: relationship_type,
          related_table: assoc.table_name,
          local_column: assoc.foreign_key,
          foreign_column: assoc.association_primary_key || 'id',
          relationship_name: assoc.name.to_s
        }
      end
    end
    
    relationships
  end
  
  def fetch_indexes(table_name)
    indexes = ActiveRecord::Base.connection.indexes(table_name)
    
    indexes.map do |index|
      {
        name: index.name,
        columns: index.columns,
        unique: index.unique
      }
    end
  end
  
  def fetch_row_count(table_name)
    # Scope count by account for tenant tables
    if has_account_id_column?(table_name)
      ActiveRecord::Base.connection.select_value(
        "SELECT COUNT(*) FROM #{table_name} WHERE account_id = #{account.id}"
      ).to_i
    else
      ActiveRecord::Base.connection.select_value(
        "SELECT COUNT(*) FROM #{table_name}"
      ).to_i
    end
  rescue
    0
  end
  
  def fetch_last_updated(table_name)
    if has_column?(table_name, 'updated_at')
      result = ActiveRecord::Base.connection.select_value(
        "SELECT MAX(updated_at) FROM #{table_name}"
      )
      result&.to_datetime
    end
  rescue
    nil
  end
  
  def fetch_primary_key(table_name)
    ActiveRecord::Base.connection.primary_key(table_name)
  end
  
  def column_description(table_name, column_name)
    case column_name
    when 'id' then "Unique identifier"
    when 'account_id' then "Reference to the account (tenant)"
    when 'hotel_id' then "Reference to the hotel"
    when 'room_id' then "Reference to the room"
    when 'guest_id' then "Reference to the guest"
    when 'user_id' then "Reference to the user"
    when 'room_type_id' then "Reference to the room type"
    when 'reservation_id' then "Reference to the reservation"
    when 'created_at' then "Record creation timestamp"
    when 'updated_at' then "Last modification timestamp"
    when 'status' then "Current status"
    when 'check_in_date' then "Date of check-in"
    when 'check_out_date' then "Date of check-out"
    when 'total_amount' then "Total amount in currency units"
    when 'paid_amount' then "Amount already paid"
    when 'confirmation_number' then "Unique booking confirmation code"
    else
      nil
    end
  end
  
  def fetch_enum_values(table_name, column_name)
    # Try to get enum values from model
    model_class = table_name.classify.safe_constantize
    return nil unless model_class
    
    if model_class.respond_to?(:defined_enums) && model_class.defined_enums[column_name]
      model_class.defined_enums[column_name].keys
    end
  rescue
    nil
  end
  
  def generate_sample_queries(table_name)
    queries = []
    
    # Basic count query
    queries << {
      description: "Count all #{table_name.humanize.downcase}",
      sql: "SELECT COUNT(*) as total FROM #{table_name}"
    }
    
    # Add specific queries based on table
    case table_name
    when 'reservations'
      queries << {
        description: "Count reservations by status",
        sql: "SELECT status, COUNT(*) as count FROM reservations GROUP BY status"
      }
      queries << {
        description: "Revenue by month",
        sql: "SELECT DATE_TRUNC('month', check_in_date) as month, SUM(total_amount) as revenue FROM reservations WHERE status != 'cancelled' GROUP BY month ORDER BY month"
      }
    when 'rooms'
      queries << {
        description: "Room availability by status",
        sql: "SELECT status, COUNT(*) as count FROM rooms GROUP BY status"
      }
    when 'guests'
      queries << {
        description: "Top 10 guests by reservation count",
        sql: "SELECT g.first_name, g.last_name, COUNT(r.id) as bookings FROM guests g JOIN reservations r ON g.id = r.guest_id GROUP BY g.id, g.first_name, g.last_name ORDER BY bookings DESC LIMIT 10"
      }
    end
    
    queries
  end
  
  def add_tenant_scoping(sql)
    # Parse SQL to identify tables
    # This is a simplified version - production would need proper SQL parsing
    tables_in_query = extract_table_names(sql)
    
    # Check if any table needs scoping
    tables_needing_scope = tables_in_query.select { |t| has_account_id_column?(t) }
    
    return sql if tables_needing_scope.empty?
    
    # Add WHERE clause for account scoping
    # This is simplified - production would need proper SQL manipulation
    if sql.downcase.include?('where')
      sql + " AND #{tables_needing_scope.first}.account_id = #{account.id}"
    else
      sql + " WHERE #{tables_needing_scope.first}.account_id = #{account.id}"
    end
  end
  
  def extract_table_names(sql)
    # Simple regex to extract table names from FROM and JOIN clauses
    # This is a simplified implementation
    tables = []
    
    # Match FROM table_name
    sql.scan(/\bfrom\s+(\w+)/i) { |match| tables << match[0] }
    
    # Match JOIN table_name
    sql.scan(/\bjoin\s+(\w+)/i) { |match| tables << match[0] }
    
    tables.uniq
  end
  
  def add_limit_to_query(sql, limit)
    # Check if query already has a LIMIT
    if sql.downcase.include?('limit')
      sql
    else
      "#{sql} LIMIT #{limit}"
    end
  end
  
  def has_account_id_column?(table_name)
    has_column?(table_name, 'account_id')
  end
  
  def has_column?(table_name, column_name)
    ActiveRecord::Base.connection.column_exists?(table_name, column_name)
  rescue
    false
  end
  
  def table_exists?(table_name)
    ActiveRecord::Base.connection.table_exists?(table_name)
  end
  
  def can_access_table?(table_name)
    fetch_accessible_tables.include?(table_name)
  end
  
  def infer_foreign_table(column_name)
    return nil unless column_name.ends_with?('_id')
    
    # Remove _id suffix and pluralize
    base_name = column_name.gsub(/_id$/, '')
    base_name.pluralize
  end
  
  def parse_sql_error(error_message, sql)
    # Extract useful information from PostgreSQL error messages
    hint = nil
    
    if error_message.include?("column") && error_message.include?("does not exist")
      hint = "Check column names and ensure they exist in the table"
    elsif error_message.include?("relation") && error_message.include?("does not exist")
      hint = "Check table name - use 'list_tables' to see available tables"
    elsif error_message.include?("syntax error")
      hint = "Check SQL syntax"
    end
    
    {
      error: error_message.split('ERROR:').last&.strip || error_message,
      hint: hint,
      query: sql
    }
  end
  
  def account_user_has_role?(*role_names)
    return false unless account_user
    
    role_names.any? do |role_name|
      account_user.has_role?(role_name)
    end
  end
end