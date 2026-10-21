module ApiTestHelper
  def create_api_agent(account, role = :front_desk)
    user = User.create!(
      email: "api-agent-#{SecureRandom.hex(4)}@test.com",
      first_name: "API",
      last_name: "Agent",
      password: SecureRandom.hex(32),
      is_ai_agent: true
    )
    
    account_user = AccountUser.create!(
      user: user,
      account: account
    )
    account_user.add_role(role)
    
    account_user
  end
  
  def api_headers_for(account_user)
    {
      'X-API-Key' => account_user.api_key,
      'Content-Type' => 'application/json',
      'Accept' => 'application/json'
    }
  end
  
  def assert_json_response
    assert_equal 'application/json', response.media_type
  end
  
  def json_response
    JSON.parse(response.body)
  end
end