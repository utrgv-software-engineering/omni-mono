# == Schema Information
#
# Table name: hotel_policies
#
#  id          :bigint           not null, primary key
#  description :text             not null
#  is_active   :boolean          default(TRUE)
#  policy_type :string           not null
#  sort_order  :integer          default(0)
#  title       :string           not null
#  created_at  :datetime         not null
#  updated_at  :datetime         not null
#  account_id  :bigint           not null
#  hotel_id    :bigint           not null
#
# Indexes
#
#  index_hotel_policies_on_account_hotel_type_active  (account_id,hotel_id,policy_type,is_active)
#  index_hotel_policies_on_account_id                 (account_id)
#  index_hotel_policies_on_hotel_id                   (hotel_id)
#
# Foreign Keys
#
#  fk_rails_...  (account_id => accounts.id)
#  fk_rails_...  (hotel_id => hotels.id)
#
require "test_helper"

class HotelPolicyTest < ActiveSupport::TestCase
  setup do
    @account = create(:account)
    set_current_tenant(@account)
    @hotel = create(:hotel, account: @account)
  end
  
  teardown do
    clear_current_tenant
  end
  
  test "activate! changes is_active to true" do
    policy = create(:hotel_policy, :inactive, hotel: @hotel, account: @account)
    assert_not policy.is_active?
    
    policy.activate!
    assert policy.is_active?
  end
  
  test "deactivate! changes is_active to false" do
    policy = create(:hotel_policy, hotel: @hotel, account: @account)
    assert policy.is_active?
    
    policy.deactivate!
    assert_not policy.is_active?
  end
  
  test "ordered scope returns policies sorted by sort_order and created_at" do
    policy1 = create(:hotel_policy, hotel: @hotel, account: @account, sort_order: 5, created_at: 2.days.ago)
    policy2 = create(:hotel_policy, hotel: @hotel, account: @account, sort_order: 1, created_at: 1.day.ago)
    policy3 = create(:hotel_policy, hotel: @hotel, account: @account, sort_order: 1, created_at: 3.days.ago)
    policy4 = create(:hotel_policy, hotel: @hotel, account: @account, sort_order: 10)
    
    ordered_policies = HotelPolicy.ordered
    
    assert_equal [policy3, policy2, policy1, policy4], ordered_policies.to_a
  end
end
