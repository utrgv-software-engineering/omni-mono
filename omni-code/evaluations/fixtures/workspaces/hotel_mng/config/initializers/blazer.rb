# Blazer configuration for multi-tenant isolation
Rails.application.config.after_initialize do
  # Override Blazer's data source to inject account context
  if defined?(Blazer)
    # Custom adapter that sets account context before queries
    class BlazerTenantAdapter < Blazer::Adapters::SqlAdapter
      def run_statement(statement, comment, bind_params = [])
        # Get the current account ID from the thread
        account_id = Thread.current[:blazer_account_id]
        
        if account_id.present?
          # Set the account context in PostgreSQL session
          connection_model.connection.execute("SET blazer.account_id = #{account_id}")
        end
        
        # Replace {account_id} placeholders in the query
        if account_id.present? && statement.include?('{account_id}')
          statement = statement.gsub('{account_id}', account_id.to_s)
        end
        
        # Run the original query
        super(statement, comment, bind_params)
      end
      
      def tables
        # Only show tables that the current account can access
        account_id = Thread.current[:blazer_account_id]
        
        if account_id.present?
          connection_model.connection.execute("SET blazer.account_id = #{account_id}")
        end
        
        super
      end
      
      def preview_statement
        # Add account filter to preview queries
        account_id = Thread.current[:blazer_account_id]
        
        if account_id.present?
          "SELECT * FROM {table} WHERE account_id = #{account_id} LIMIT 10"
        else
          super
        end
      end
    end
    
    # Register our custom adapter
    Blazer.register_adapter "tenant_sql", BlazerTenantAdapter
    
    # Modify the default data source to use our adapter
    Blazer.settings["data_sources"]["main"]["adapter"] = "tenant_sql"
    
    # Use the display_name method from User model
    # This is configured in blazer.yml as well
    Blazer.user_name = :display_name
  end
end