require 'faye/websocket'
require 'eventmachine'
require 'json'

class AgentRpcClient
  attr_reader :session_id, :ws, :pending_messages, :server_url, :token

  def initialize(session_id: nil, server_url: nil, token: nil)
    @session_id = session_id
    @server_url = server_url
    @token = token
    @callbacks = {}
    @event_handlers = {}
    @pending_messages = []
    @connected = false
    @run_id = nil
    @message_id = 0
  end
  
  # For platform-wide deployments (always enabled)
  def self.for_platform_deployment(deployment_key, session_id: nil)
    config = AiDeployments.get_platform(deployment_key)
    return nil unless config
    
    new(
      server_url: config[:agent_url],
      token: config[:token],
      session_id: session_id
    )
  end
  
  # For hotel-specific deployments (hotel-controlled)
  def self.for_hotel_deployment(hotel, deployment_key, session_id: nil)
    return nil unless AiDeployments.hotel_enabled?(hotel, deployment_key)
    
    config = AiDeployments.get_hotel(deployment_key)
    return nil unless config
    
    new(
      server_url: config[:agent_url],
      token: config[:token],
      session_id: session_id
    )
  end

  def connect(&block)
    return if @connected
    
    raise ArgumentError, "server_url is required" unless @server_url.present?

    EM.run do
      @ws = Faye::WebSocket::Client.new(@server_url)

      @ws.on :open do |event|
        Rails.logger.info "[AgentRPC] Connected to agent server"
        @connected = true
        block.call if block
      end

      @ws.on :message do |event|
        handle_message(JSON.parse(event.data))
      end

      @ws.on :close do |event|
        Rails.logger.info "[AgentRPC] Disconnected: #{event.code} #{event.reason}"
        @connected = false
        @ws = nil
      end

      @ws.on :error do |event|
        Rails.logger.error "[AgentRPC] Error: #{event.message}"
      end
    end
  end

  def start_run(prompt, &block)
    @message_id += 1
    call_id = @message_id.to_s
    
    # Store callback for when we get the response
    @callbacks[call_id] = block if block

    rpc_call = {
      jsonrpc: "2.0",
      id: call_id,
      method: "start_run",
      params: {
        prompt: prompt,
        session_id: @session_id
      }
    }

    send_message(rpc_call)
  end

  def on_event(event_type, &block)
    @event_handlers[event_type] = block
  end

  def disconnect
    @ws&.close
    EM.stop if EM.reactor_running?
  end

  private

  def send_message(data)
    if @ws && @connected
      @ws.send(JSON.generate(data))
    else
      @pending_messages << data
    end
  end

  def handle_message(data)
    # Handle RPC responses
    if data["id"] && data["result"]
      call_id = data["id"].to_s
      if callback = @callbacks.delete(call_id)
        result = data["result"]
        @session_id ||= result["session_id"] if result.is_a?(Hash)
        @run_id = result["run_id"] if result.is_a?(Hash)
        callback.call(result)
      end
    end

    # Handle event notifications
    if data["method"]
      case data["method"]
      when "tool_called"
        trigger_event(:tool_called, data["params"])
      when "tool_result"
        trigger_event(:tool_result, data["params"])
      when "message_output"
        trigger_event(:message_output, data["params"])
      when "run_end"
        trigger_event(:run_end, data["params"])
        @run_id = nil
      when "tool_approval_request"
        trigger_event(:tool_approval_request, data["params"])
      end
    end
  end

  def trigger_event(event_type, params)
    if handler = @event_handlers[event_type]
      handler.call(params)
    end
  end
end