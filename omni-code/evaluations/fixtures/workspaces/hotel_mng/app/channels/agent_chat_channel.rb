class AgentChatChannel < ApplicationCable::Channel
  def subscribed
    # Create or retrieve chat session
    @chat_session = find_or_create_session
    
    # If no session (hotel not found), reject the connection
    unless @chat_session
      reject
      return
    end
    
    stream_for @chat_session
    
    # Send welcome message
    welcome_data = {
      type: "connected", 
      session_id: @chat_session.session_id
    }
    welcome_data[:hotel_name] = @chat_session.hotel.name if @chat_session.hotel
    
    transmit(welcome_data)
    
    # Initialize agent connection in background
    initialize_agent_connection
  end

  def unsubscribed
    # Clean up agent connection
    if @agent_client
      @agent_client.disconnect
    end
  end

  def send_message(data)
    message = data["message"]
    return if message.blank?
    
    # Save user message
    @chat_session.chat_messages.create!(
      role: "user",
      content: message
    )
    
    # Send to agent and stream response
    stream_agent_response(message)
  end

  def typing(data)
    # Broadcast typing indicator to other connected clients if needed
    AgentChatChannel.broadcast_to(@chat_session, type: "typing", typing: data["typing"])
  end

  private

  def find_or_create_session
    session_id = params[:session_id]
    hotel_id = params[:hotel_id]
    deployment_type = params[:deployment_type]
    deployment_key = params[:deployment_key]
    
    # Deployment parameters are required
    unless deployment_type.present? && deployment_key.present?
      reject
      return nil
    end
    
    if deployment_type == 'hotel'
      # Hotel is required for hotel deployments
      unless hotel_id.present?
        reject
        return nil
      end
      
      # Check if hotel has this deployment enabled
      hotel = Hotel.find_by(id: hotel_id)
      unless hotel && AiDeployments.hotel_enabled?(hotel, deployment_key)
        reject
        return nil
      end
      
      if session_id.present?
        # Find existing session and verify it belongs to the correct hotel
        session = ChatSession.find_by(session_id: session_id, hotel_id: hotel_id)
        session || create_new_session
      else
        create_new_session
      end
    else
      # Platform deployment - no hotel required
      if session_id.present?
        ChatSession.find_by(session_id: session_id, hotel_id: nil)
      else
        create_new_session
      end
    end
  end

  def create_new_session
    deployment_type = params[:deployment_type]
    
    if deployment_type == 'hotel'
      # Get hotel from params (required for hotel deployments)
      hotel = Hotel.find_by(id: params[:hotel_id])
      
      unless hotel
        reject
        return nil
      end
      
      ChatSession.create!(
        hotel: hotel
      )
    else
      # Platform deployment - no hotel
      ChatSession.create!(
        hotel: nil
      )
    end
  end

  def initialize_agent_connection
    deployment_type = params[:deployment_type]
    deployment_key = params[:deployment_key]
    
    @agent_client = case deployment_type
    when 'platform'
      AgentRpcClient.for_platform_deployment(deployment_key, session_id: @chat_session.session_id)
    when 'hotel'
      hotel = @chat_session.hotel
      AgentRpcClient.for_hotel_deployment(hotel, deployment_key, session_id: @chat_session.session_id) if hotel
    end
    
    unless @agent_client
      reject
      return
    end
    
    # Set up event handlers
    @agent_client.on_event(:message_output) do |params|
      content = params["content"] || params["text"]
      if content
        # Save agent message
        @chat_session.chat_messages.create!(
          role: "agent",
          content: content,
          metadata: params.except("content", "text")
        )
        
        # Broadcast to client
        AgentChatChannel.broadcast_to(@chat_session, {
          type: "message",
          role: "agent",
          content: content
        })
      end
    end
    
    @agent_client.on_event(:tool_called) do |params|
      AgentChatChannel.broadcast_to(@chat_session, {
        type: "tool_called",
        tool: params["tool"],
        status: "calling"
      })
    end
    
    @agent_client.on_event(:tool_result) do |params|
      AgentChatChannel.broadcast_to(@chat_session, {
        type: "tool_result",
        tool: params["tool"],
        status: "complete"
      })
    end
    
    @agent_client.on_event(:run_end) do |params|
      AgentChatChannel.broadcast_to(@chat_session, {
        type: "run_end"
      })
    end
    
    # Connect in background
    Thread.new do
      @agent_client.connect
    end
  end

  def stream_agent_response(message)
    # Start agent run in background job to avoid blocking
    deployment_type = params[:deployment_type]
    deployment_key = params[:deployment_key]
    AgentChatJob.perform_later(@chat_session.id, message, deployment_type, deployment_key)
  end
end
