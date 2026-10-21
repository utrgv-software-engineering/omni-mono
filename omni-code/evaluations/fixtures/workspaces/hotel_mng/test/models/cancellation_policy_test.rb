# == Schema Information
#
# Table name: cancellation_policies
#
#  id                      :bigint           not null, primary key
#  cancellation_type       :string           not null
#  code                    :string           not null
#  description             :text
#  free_cancellation_hours :integer
#  is_default              :boolean          default(FALSE)
#  is_refundable           :boolean          default(TRUE)
#  name                    :string           not null
#  no_show_penalty         :decimal(5, 2)
#  penalty_nights          :integer
#  penalty_percentage      :decimal(5, 2)
#  created_at              :datetime         not null
#  updated_at              :datetime         not null
#  account_id              :bigint           not null
#  hotel_id                :bigint
#
# Indexes
#
#  index_cancellation_policies_on_account_hotel_default  (account_id,hotel_id,is_default)
#  index_cancellation_policies_on_account_id             (account_id)
#  index_cancellation_policies_on_hotel_id               (hotel_id)
#
# Foreign Keys
#
#  fk_rails_...  (account_id => accounts.id)
#  fk_rails_...  (hotel_id => hotels.id)
#
require "test_helper"

class CancellationPolicyTest < ActiveSupport::TestCase
  setup do
    @account = create(:account)
    set_current_tenant(@account)
    @hotel = create(:hotel, account: @account)
    @room_type = create(:room_type, hotel: @hotel, account: @account, base_price: 100)
    @guest = create(:guest, account: @account)
  end
  
  teardown do
    clear_current_tenant
  end
  
  test "calculate_cancellation_fee for flexible policy" do
    policy = create(:cancellation_policy, :flexible, account: @account)
    reservation = create(:reservation, 
                        hotel: @hotel, 
                        account: @account,
                        room_type: @room_type,
                        guest: @guest,
                        check_in_date: 3.days.from_now,
                        check_out_date: 5.days.from_now,
                        total_amount: 200,
                        paid_amount: 200)
    
    # Free cancellation more than 24 hours before
    fee = policy.calculate_cancellation_fee(reservation, Date.current)
    assert_equal 0, fee
    
    # Penalty applies less than 24 hours before
    fee = policy.calculate_cancellation_fee(reservation, 2.days.from_now)
    assert_equal 100, fee # 1 night penalty
  end
  
  test "calculate_cancellation_fee for moderate policy" do
    policy = create(:cancellation_policy, :moderate, account: @account)
    reservation = create(:reservation, 
                        hotel: @hotel, 
                        account: @account,
                        room_type: @room_type,
                        guest: @guest,
                        check_in_date: 7.days.from_now,
                        check_out_date: 10.days.from_now,
                        total_amount: 300,
                        paid_amount: 300)
    
    # Free cancellation 5+ days before
    fee = policy.calculate_cancellation_fee(reservation, Date.current)
    assert_equal 0, fee
    
    # One night charge 1-4 days before
    fee = policy.calculate_cancellation_fee(reservation, 4.days.from_now)
    assert_equal 100, fee
    
    # Full charge less than 1 day before
    fee = policy.calculate_cancellation_fee(reservation, 7.days.from_now)
    assert_equal 300, fee
  end
  
  test "calculate_cancellation_fee for strict policy" do
    policy = create(:cancellation_policy, :strict, account: @account)
    reservation = create(:reservation, 
                        hotel: @hotel, 
                        account: @account,
                        room_type: @room_type,
                        guest: @guest,
                        check_in_date: 10.days.from_now,
                        check_out_date: 12.days.from_now,
                        total_amount: 200,
                        paid_amount: 200)
    
    # 50% charge 7+ days before
    fee = policy.calculate_cancellation_fee(reservation, Date.current)
    assert_equal 100, fee
    
    # Full charge less than 7 days before
    fee = policy.calculate_cancellation_fee(reservation, 5.days.from_now)
    assert_equal 200, fee
  end
  
  test "calculate_cancellation_fee for non_refundable policy" do
    policy = create(:cancellation_policy, :non_refundable, account: @account)
    reservation = create(:reservation, 
                        hotel: @hotel, 
                        account: @account,
                        room_type: @room_type,
                        guest: @guest,
                        check_in_date: 10.days.from_now,
                        check_out_date: 12.days.from_now,
                        total_amount: 200,
                        paid_amount: 200)
    
    # Always full charge for non-refundable
    fee = policy.calculate_cancellation_fee(reservation, Date.current)
    assert_equal 200, fee
  end
  
  test "calculate_no_show_fee applies penalty percentage" do
    policy = create(:cancellation_policy, account: @account, no_show_penalty: 50)
    reservation = create(:reservation, 
                        hotel: @hotel, 
                        account: @account,
                        room_type: @room_type,
                        guest: @guest,
                        total_amount: 200)
    
    fee = policy.calculate_no_show_fee(reservation)
    assert_equal 100, fee
  end
  
  test "make_default! ensures only one default per scope" do
    # Account-wide policies
    policy1 = create(:cancellation_policy, account: @account, is_default: true)
    policy2 = create(:cancellation_policy, account: @account, is_default: false)
    
    assert policy1.is_default?
    assert_not policy2.is_default?
    
    policy2.make_default!
    
    policy1.reload
    assert_not policy1.is_default?
    assert policy2.is_default?
    
    # Hotel-specific policies
    hotel_policy1 = create(:cancellation_policy, account: @account, hotel: @hotel, is_default: true)
    hotel_policy2 = create(:cancellation_policy, account: @account, hotel: @hotel, is_default: false)
    
    hotel_policy2.make_default!
    
    hotel_policy1.reload
    assert_not hotel_policy1.is_default?
    assert hotel_policy2.is_default?
    
    # Account-wide default should remain unchanged
    policy2.reload
    assert policy2.is_default?
  end
  
  test "ensure_single_default_per_scope on save" do
    policy1 = create(:cancellation_policy, account: @account, is_default: true)
    policy2 = create(:cancellation_policy, account: @account, is_default: true)
    
    policy1.reload
    assert_not policy1.is_default?
    assert policy2.is_default?
  end
end
