import unittest
from datetime import datetime, timezone
from src.models.benefit import Benefit, BenefitType
from src.models.drop import TimedDrop
from src.models.campaign import DropsCampaign

class TestBenefitFilter(unittest.TestCase):
    def setUp(self):
        # Mock Benefit data
        self.benefit_badge_data = {
            "benefit": {
                "id": "b1",
                "name": "Test Badge",
                "distributionType": "BADGE",
                "imageAssetURL": "url"
            }
        }
        self.benefit_item_data = {
            "benefit": {
                "id": "b2",
                "name": "Test Item",
                "distributionType": "DIRECT_ENTITLEMENT",
                "imageAssetURL": "url"
            }
        }
        
        # Initialize Benefits
        self.badge = Benefit(self.benefit_badge_data)
        self.item = Benefit(self.benefit_item_data)

    def test_benefit_is_wanted(self):
        allowed = {"BADGE": True, "DIRECT_ENTITLEMENT": False}
        self.assertTrue(self.badge.is_wanted(allowed))
        self.assertFalse(self.item.is_wanted(allowed))
        
        allowed_all = {"BADGE": True, "DIRECT_ENTITLEMENT": True}
        self.assertTrue(self.badge.is_wanted(allowed_all))
        self.assertTrue(self.item.is_wanted(allowed_all))

    def test_drop_has_wanted_unclaimed_benefits(self):
        # Mock TimedDrop
        # functionality relies on self.benefits and self.is_claimed
        
        # 1. Unclaimed Drop with only Badge
        drop1 = unittest.mock.MagicMock(spec=TimedDrop)
        drop1.is_claimed = False
        drop1.benefits = [self.badge]
        drop1.has_wanted_unclaimed_benefits = TimedDrop.has_wanted_unclaimed_benefits.__get__(drop1)
        
        allowed = {"BADGE": True, "DIRECT_ENTITLEMENT": False}
        self.assertTrue(drop1.has_wanted_unclaimed_benefits(allowed))
        
        allowed_none = {"BADGE": False, "DIRECT_ENTITLEMENT": False}
        self.assertFalse(drop1.has_wanted_unclaimed_benefits(allowed_none))

        # 2. Claimed Drop with Badge
        drop2 = unittest.mock.MagicMock(spec=TimedDrop)
        drop2.is_claimed = True
        drop2.benefits = [self.badge]
        drop2.has_wanted_unclaimed_benefits = TimedDrop.has_wanted_unclaimed_benefits.__get__(drop2)
        
        self.assertFalse(drop2.has_wanted_unclaimed_benefits(allowed))

        # 3. Drops with mixed benefits
        drop3 = unittest.mock.MagicMock(spec=TimedDrop)
        drop3.is_claimed = False
        drop3.benefits = [self.badge, self.item]
        drop3.has_wanted_unclaimed_benefits = TimedDrop.has_wanted_unclaimed_benefits.__get__(drop3)
        
        # Only want Item (which it has)
        allowed_item = {"BADGE": False, "DIRECT_ENTITLEMENT": True}
        self.assertTrue(drop3.has_wanted_unclaimed_benefits(allowed_item))

    def test_campaign_has_wanted_unclaimed_benefits(self):
        # Mock DropsCampaign
        campaign = unittest.mock.MagicMock(spec=DropsCampaign)
        
        drop1 = unittest.mock.MagicMock(spec=TimedDrop)
        drop1.has_wanted_unclaimed_benefits.return_value = False
        
        drop2 = unittest.mock.MagicMock(spec=TimedDrop)
        drop2.has_wanted_unclaimed_benefits.return_value = True
        
        campaign.drops = [drop1, drop2]
        # Bind method
        campaign.has_wanted_unclaimed_benefits = DropsCampaign.has_wanted_unclaimed_benefits.__get__(campaign)
        
        allowed = {"BADGE": True} 
        # Since drop2 returns True, campaign should return True
        self.assertTrue(campaign.has_wanted_unclaimed_benefits(allowed))
        
        # Case where all drops return False
        drop2.has_wanted_unclaimed_benefits.return_value = False
        self.assertFalse(campaign.has_wanted_unclaimed_benefits(allowed))

if __name__ == '__main__':
    unittest.main()
