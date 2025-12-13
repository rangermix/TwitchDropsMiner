
import unittest
from unittest.mock import MagicMock
from datetime import datetime, timezone

from src.models.benefit import Benefit, BenefitType
from src.models.drop import BaseDrop, TimedDrop
from src.models.campaign import DropsCampaign


class TestSkipBadgeOnlyDrops(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures."""
        # Mock Twitch client
        self.mock_twitch = MagicMock()
        self.mock_settings = MagicMock()
        self.mock_twitch.settings = self.mock_settings
        
        # Mock campaign
        self.mock_campaign = MagicMock(spec=DropsCampaign)
        self.mock_campaign._twitch = self.mock_twitch
        self.mock_campaign.id = "test-campaign-id"
        self.mock_campaign.preconditions_chain = MagicMock(return_value=[])
        
        # Sample drop data with badge benefits
        self.badge_drop_data = {
            "id": "drop-badge-1",
            "name": "Badge Drop",
            "benefitEdges": [
                {
                    "benefit": {
                        "id": "benefit-1",
                        "name": "Test Badge",
                        "distributionType": "BADGE",
                        "imageAssetURL": "http://example.com/badge.png"
                    }
                }
            ],
            "startAt": "2024-01-01T00:00:00Z",
            "endAt": "2024-12-31T23:59:59Z",
            "preconditionDrops": []
        }
        
        # Sample drop data with emote benefits
        self.emote_drop_data = {
            "id": "drop-emote-1",
            "name": "Emote Drop",
            "benefitEdges": [
                {
                    "benefit": {
                        "id": "benefit-2",
                        "name": "Test Emote",
                        "distributionType": "EMOTE",
                        "imageAssetURL": "http://example.com/emote.png"
                    }
                }
            ],
            "startAt": "2024-01-01T00:00:00Z",
            "endAt": "2024-12-31T23:59:59Z",
            "preconditionDrops": []
        }
        
        # Sample drop data with mixed benefits (badge + item)
        self.mixed_drop_data = {
            "id": "drop-mixed-1",
            "name": "Mixed Drop",
            "benefitEdges": [
                {
                    "benefit": {
                        "id": "benefit-3",
                        "name": "Test Badge",
                        "distributionType": "BADGE",
                        "imageAssetURL": "http://example.com/badge.png"
                    }
                },
                {
                    "benefit": {
                        "id": "benefit-4",
                        "name": "Test Item",
                        "distributionType": "DIRECT_ENTITLEMENT",
                        "imageAssetURL": "http://example.com/item.png"
                    }
                }
            ],
            "startAt": "2024-01-01T00:00:00Z",
            "endAt": "2024-12-31T23:59:59Z",
            "preconditionDrops": []
        }
        
        # Sample drop data with only item benefits
        self.item_drop_data = {
            "id": "drop-item-1",
            "name": "Item Drop",
            "benefitEdges": [
                {
                    "benefit": {
                        "id": "benefit-5",
                        "name": "Test Item",
                        "distributionType": "DIRECT_ENTITLEMENT",
                        "imageAssetURL": "http://example.com/item.png"
                    }
                }
            ],
            "startAt": "2024-01-01T00:00:00Z",
            "endAt": "2024-12-31T23:59:59Z",
            "preconditionDrops": []
        }
        
        # Sample timed drop data with badge benefits
        self.timed_badge_drop_data = {
            "id": "timed-drop-badge-1",
            "name": "Timed Badge Drop",
            "requiredMinutesWatched": 30,
            "benefitEdges": [
                {
                    "benefit": {
                        "id": "benefit-6",
                        "name": "Timed Badge",
                        "distributionType": "BADGE",
                        "imageAssetURL": "http://example.com/timed_badge.png"
                    }
                }
            ],
            "startAt": "2024-01-01T00:00:00Z",
            "endAt": "2024-12-31T23:59:59Z",
            "preconditionDrops": []
        }

    def test_skip_badge_only_drops_disabled(self):
        """Test that badge-only drops are allowed when skip_badge_only_drops is False."""
        self.mock_settings.skip_badge_only_drops = False
        
        drop = BaseDrop(self.mock_campaign, self.badge_drop_data, {})
        
        # Should be allowed since setting is disabled
        result = drop._base_earn_conditions()
        self.assertTrue(result)

    def test_skip_badge_only_drops_enabled_badge_drop(self):
        """Test that badge-only drops are skipped when skip_badge_only_drops is True."""
        self.mock_settings.skip_badge_only_drops = True
        
        drop = BaseDrop(self.mock_campaign, self.badge_drop_data, {})
        
        # Should be skipped since it only has badge benefits
        result = drop._base_earn_conditions()
        self.assertFalse(result)

    def test_skip_badge_only_drops_enabled_emote_drop(self):
        """Test that emote-only drops are skipped when skip_badge_only_drops is True."""
        self.mock_settings.skip_badge_only_drops = True
        
        drop = BaseDrop(self.mock_campaign, self.emote_drop_data, {})
        
        # Should be skipped since it only has emote benefits
        result = drop._base_earn_conditions()
        self.assertFalse(result)

    def test_skip_badge_only_drops_enabled_mixed_drop(self):
        """Test that drops with mixed benefits are NOT skipped when skip_badge_only_drops is True."""
        self.mock_settings.skip_badge_only_drops = True
        
        drop = BaseDrop(self.mock_campaign, self.mixed_drop_data, {})
        
        # Should NOT be skipped since it has non-badge/emote benefits
        result = drop._base_earn_conditions()
        self.assertTrue(result)

    def test_skip_badge_only_drops_enabled_item_drop(self):
        """Test that item drops are NOT skipped when skip_badge_only_drops is True."""
        self.mock_settings.skip_badge_only_drops = True
        
        drop = BaseDrop(self.mock_campaign, self.item_drop_data, {})
        
        # Should NOT be skipped since it has item benefits
        result = drop._base_earn_conditions()
        self.assertTrue(result)

    def test_skip_badge_only_drops_timed_drop(self):
        """Test that timed badge-only drops are skipped when skip_badge_only_drops is True."""
        self.mock_settings.skip_badge_only_drops = True
        
        timed_drop = TimedDrop(self.mock_campaign, self.timed_badge_drop_data, {})
        
        # Should be skipped since it only has badge benefits
        result = timed_drop._base_earn_conditions()
        self.assertFalse(result)

    def test_benefit_type_is_badge_or_emote(self):
        """Test that the is_badge_or_emote method correctly identifies badge and emote types."""
        self.assertTrue(BenefitType.BADGE.is_badge_or_emote())
        self.assertTrue(BenefitType.EMOTE.is_badge_or_emote())
        self.assertFalse(BenefitType.DIRECT_ENTITLEMENT.is_badge_or_emote())
        self.assertFalse(BenefitType.UNKNOWN.is_badge_or_emote())


if __name__ == "__main__":
    unittest.main()
