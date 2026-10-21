class Api::V1::DataAgentController < Api::V1::BaseController
  # Data agent endpoints don't require hotel context

  # GET /api/v1/data_agent/tables
  def tables
    authorize :data_agent, :list_tables?
    
    service = DataAgentService.new(@current_account, @current_account_user)
    result = service.list_tables
    
    render json: { data: result }
  end
  
  # GET /api/v1/data_agent/tables/:table_name
  def describe_table
    authorize :data_agent, :describe_table?
    
    table_name = params[:table_name]
    
    unless table_name.present?
      render_error("Table name is required", status: :bad_request)
      return
    end
    
    service = DataAgentService.new(@current_account, @current_account_user)
    result = service.describe_table(table_name)
    
    if result[:error]
      render_error(result[:error], status: :not_found)
    else
      render json: { data: result }
    end
  end
  
  # POST /api/v1/data_agent/preview_query
  def preview_query
    authorize :data_agent, :preview_query?
    
    sql = params[:sql]
    max_rows = params[:max_rows] || 100
    
    # Validate required parameters
    unless sql.present?
      render_error("SQL query is required", status: :bad_request)
      return
    end
    
    # Validate max_rows
    max_rows = max_rows.to_i
    if max_rows <= 0
      render_error("max_rows must be greater than 0", status: :bad_request)
      return
    end
    
    if max_rows > 1000
      max_rows = 1000
    end
    
    service = DataAgentService.new(@current_account, @current_account_user)
    result = service.execute_query(sql, max_rows: max_rows)
    
    if result[:error]
      render json: {
        error: "Query Error",
        message: result[:error],
        status: 400,
        query: sql,
        hint: result[:hint]
      }, status: :bad_request
    elsif result[:timeout]
      render json: {
        error: "Query Timeout",
        message: "Query execution exceeded 30 second timeout",
        status: 408
      }, status: :request_timeout
    else
      render json: { data: result }
    end
  end
  
  private
  
  def render_error(message, status: :bad_request)
    status_code = Rack::Utils::SYMBOL_TO_STATUS_CODE[status]
    render json: {
      error: status.to_s.humanize,
      message: message,
      status: status_code
    }, status: status
  end
end