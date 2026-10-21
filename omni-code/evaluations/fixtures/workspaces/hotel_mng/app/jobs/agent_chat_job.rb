class AgentChatJob < ApplicationJob
  queue_as :default

  def perform(chat_session_id, message, deployment_type = nil, deployment_key = nil)
    chat_session = ChatSession.find(chat_session_id)
    
    # Create agent client based on deployment type
    agent_client = if chat_session.platform_session?
      AgentRpcClient.for_platform_deployment(deployment_key, session_id: chat_session.session_id)
    else
      AgentRpcClient.for_hotel_deployment(chat_session.hotel, deployment_key, session_id: chat_session.session_id)
    end
    
    unless agent_client
      Rails.logger.error "[AgentChat] Failed to create agent client for deployment: #{deployment_type}/#{deployment_key}"
      raise "Failed to create agent client"
    end
    
    # Track if we've received any response
    response_received = false
    
    # Set up event handlers
    agent_client.on_event(:message_output) do |params|
      content = params["content"] || params["text"]
      if content
        response_received = true
        
        # Save agent message
        chat_session.chat_messages.create!(
          role: "agent",
          content: content,
          metadata: params.except("content", "text")
        )
        
        # Broadcast to client
        AgentChatChannel.broadcast_to(chat_session, {
          type: "message",
          role: "agent",
          content: content
        })
      end
    end
    
    agent_client.on_event(:tool_called) do |params|
      AgentChatChannel.broadcast_to(chat_session, {
        type: "tool_called",
        tool: params["tool"],
        status: "calling"
      })
    end
    
    agent_client.on_event(:tool_result) do |params|
      AgentChatChannel.broadcast_to(chat_session, {
        type: "tool_result",
        tool: params["tool"],
        status: "complete"
      })
    end
    
    agent_client.on_event(:run_end) do |params|
      AgentChatChannel.broadcast_to(chat_session, {
        type: "run_end"
      })
    end
    
    # Connect and send message
    agent_client.connect do
      agent_client.start_run(message) do |result|
        Rails.logger.info "[AgentChat] Run started: #{result.inspect}"
      end
    end
    
    # Wait for response with timeout
    timeout = 30.seconds
    start_time = Time.current
    
    while !response_received && (Time.current - start_time) < timeout
      sleep 0.1
    end
    
    # Clean up
    agent_client.disconnect
    
  rescue => e
    Rails.logger.error "[AgentChat] Error: #{e.message}"
    
    # Send error message to client
    AgentChatChannel.broadcast_to(chat_session, {
      type: "error",
      message: "Sorry, I'm having trouble connecting right now. Please try again later."
    })
  end
end