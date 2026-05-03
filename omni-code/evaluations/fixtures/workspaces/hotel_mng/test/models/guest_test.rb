# == Schema Information
#
# Table name: guests
#
#  id              :bigint           not null, primary key
#  address         :string
#  city            :string
#  country         :string
#  date_of_birth   :date
#  email           :string           not null
#  first_name      :string           not null
#  id_number       :string
#  last_name       :string           not null
#  nationality     :string
#  notes           :text
#  passport_number :string
#  phone           :string
#  postal_code     :string
#  state           :string
#  vip_status      :boolean          default(FALSE)
#  created_at      :datetime         not null
#  updated_at      :datetime         not null
#  account_id      :bigint           not null
#
# Indexes
#
#  index_guests_on_account_id                (account_id)
#  index_guests_on_account_id_and_email      (account_id,email)
#  index_guests_on_account_id_and_last_name  (account_id,last_name)
#
# Foreign Keys
#
#  fk_rails_...  (account_id => accounts.id)
#
require "test_helper"

class GuestTest < ActiveSupport::TestCase
  setup do
    @account = create(:account)
    set_current_tenant(@account)
    @hotel = create(:hotel, account: @account)
    @guest = create(:guest, account: @account, date_of_birth: 30.years.ago)
  end
  
  teardown do
    clear_current_tenant
  end
  
  test "calculates age correctly" do
    guest = create(:guest, account: @account, date_of_birth: 25.years.ago)
    assert_in_delta 25, guest.age, 1
  end
  
  test "returns nil age when no date of birth" do
    guest = create(:guest, account: @account, date_of_birth: nil)
    assert_nil guest.age
  end
  
  test "determines if guest is adult" do
    adult_guest = create(:guest, account: @account, date_of_birth: 25.years.ago)
    child_guest = create(:guest, account: @account, date_of_birth: 10.years.ago)
    no_dob_guest = create(:guest, account: @account, date_of_birth: nil)
    
    assert adult_guest.adult?
    assert_not child_guest.adult?
    assert_not no_dob_guest.adult?
  end
  
  test "loyalty status based on total stays" do
    room_type = create(:room_type, hotel: @hotel, account: @account)
    
    # New guest (0 stays)
    assert_equal "New", @guest.loyalty_status
    
    # Create some checked out reservations
    3.times do
      create(:reservation, :checked_out, guest: @guest, hotel: @hotel, 
             account: @account, room_type: room_type)
    end
    assert_equal "Regular", @guest.loyalty_status
    
    # Add more stays
    3.times do
      create(:reservation, :checked_out, guest: @guest, hotel: @hotel,
             account: @account, room_type: room_type)
    end
    assert_equal "Frequent", @guest.loyalty_status
    
    # Add even more stays
    5.times do
      create(:reservation, :checked_out, guest: @guest, hotel: @hotel,
             account: @account, room_type: room_type)
    end
    assert_equal "Elite", @guest.loyalty_status
  end
  
  test "counts total stays correctly" do
    room_type = create(:room_type, hotel: @hotel, account: @account)
    
    # Create different status reservations
    create(:reservation, :checked_out, guest: @guest, hotel: @hotel, 
           account: @account, room_type: room_type)
    create(:reservation, :checked_in, guest: @guest, hotel: @hotel,
           account: @account, room_type: room_type)
    create(:reservation, :confirmed, guest: @guest, hotel: @hotel,
           account: @account, room_type: room_type)
    create(:reservation, :cancelled, guest: @guest, hotel: @hotel,
           account: @account, room_type: room_type)
    
    # Should count only checked_out and checked_in
    assert_equal 2, @guest.total_stays
  end
  
  test "calculates total spent" do
    room_type = create(:room_type, hotel: @hotel, account: @account)
    
    create(:reservation, :checked_out, guest: @guest, hotel: @hotel,
           account: @account, room_type: room_type, total_amount: 500)
    create(:reservation, :checked_out, guest: @guest, hotel: @hotel,
           account: @account, room_type: room_type, total_amount: 300)
    create(:reservation, :checked_in, guest: @guest, hotel: @hotel,
           account: @account, room_type: room_type, total_amount: 400) # Not counted
    
    assert_equal 800, @guest.total_spent
  end
  
  test "finds last stay" do
    room_type = create(:room_type, hotel: @hotel, account: @account)
    
    old_stay = create(:reservation, :checked_out, guest: @guest, hotel: @hotel,
                     account: @account, room_type: room_type,
                     check_in_date: 15.days.ago, check_out_date: 10.days.ago)
    recent_stay = create(:reservation, :checked_out, guest: @guest, hotel: @hotel,
                        account: @account, room_type: room_type,
                        check_in_date: 5.days.ago, check_out_date: 2.days.ago)
    
    assert_equal recent_stay, @guest.last_stay
  end
  
  test "finds upcoming reservations" do
    room_type = create(:room_type, hotel: @hotel, account: @account)
    
    future = create(:reservation, :confirmed, guest: @guest, hotel: @hotel,
                   account: @account, room_type: room_type,
                   check_in_date: 5.days.from_now, check_out_date: 8.days.from_now)
    past = create(:reservation, :confirmed, guest: @guest, hotel: @hotel,
                 account: @account, room_type: room_type,
                 check_in_date: 5.days.ago, check_out_date: 2.days.ago)
    cancelled = create(:reservation, :cancelled, guest: @guest, hotel: @hotel,
                      account: @account, room_type: room_type,
                      check_in_date: 10.days.from_now, check_out_date: 12.days.from_now)
    
    upcoming = @guest.upcoming_reservations
    assert_includes upcoming, future
    assert_not_includes upcoming, past
    assert_not_includes upcoming, cancelled
  end
  
  test "finds current reservation" do
    room_type = create(:room_type, hotel: @hotel, account: @account)
    
    current = create(:reservation, :checked_in, guest: @guest, hotel: @hotel,
                    account: @account, room_type: room_type)
    other = create(:reservation, :confirmed, guest: @guest, hotel: @hotel,
                  account: @account, room_type: room_type)
    
    assert_equal current, @guest.current_reservation
  end
  
  test "display name includes VIP status" do
    regular_guest = create(:guest, account: @account, first_name: "John", 
                          last_name: "Doe", vip_status: false)
    vip_guest = create(:guest, :vip, account: @account, first_name: "Jane",
                      last_name: "Smith")
    
    assert_equal "John Doe", regular_guest.display_name
    assert_equal "Jane Smith (VIP)", vip_guest.display_name
  end
end
