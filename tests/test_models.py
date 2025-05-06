import json
from typing import Any, cast
from unittest.mock import patch

import pytest
import yaml
from pydantic import HttpUrl, ValidationError

from awsmp import _driver, models


@pytest.fixture
def mock_boto3():
    with patch("awsmp.models.boto3") as mock_boto3:
        mock_boto3.client.return_value.describe_regions.return_value = {
            "Regions": [
                {"Endpoint": "ec2.us-east-1.amazonaws.com", "RegionName": "us-east-1", "OptInStatus": "opted-in"},
                {"Endpoint": "ec2.us-east-2.amazonaws.com", "RegionName": "us-east-2", "OptInStatus": "opted-in"},
            ]
        }
        yield mock_boto3


class TestAmiDescriptionSuite:
    def _build_ami_description(self, **kwargs):
        defaults = dict(
            product_title="p" * 72,
            short_description="short_description",
            long_description="long_descrption",
            logourl="https://some-url",
            highlights=["highlight1"],
            categories=["Storage"],
            search_keywords=["one_term"],
            support_description="supported!",
        )
        return models.Description(**(defaults | kwargs))

    @pytest.mark.parametrize(
        "provided_keys,expected",
        [
            ([], []),
            ([{"some_key": "http://some_value"}], [{"Text": "some_key", "Url": "http://some_value/"}]),
            (
                [{f"k{i}": f"http://url{i}/"} for i in range(3)],
                [{"Text": f"k{i}", "Url": f"http://url{i}/"} for i in range(3)],
            ),
        ],
    )
    def test_should_convert_additional_resources_to_api_format(self, provided_keys, expected):
        product = self._build_ami_description(additional_resources=provided_keys)
        assert product.additional_resources == expected

    @pytest.mark.parametrize("ami_product_field", ["support_description", "long_description"])
    def test_should_strip_new_lines_from_relevent_fields(self, ami_product_field):
        valid_description = "my description\n\nafter separator"
        product = self._build_ami_description(**{ami_product_field: f"\n\n\n{valid_description}\n\n"})
        assert getattr(product, ami_product_field) == valid_description

    def test_search_keywords_should_not_accept_large_input(self):
        keywords = ["a" * 150, "b" * 105, "c", "d", "e"]
        with pytest.raises(ValueError) as e:
            self._build_ami_description(search_keywords=keywords)
        err = "Combined character count of keywords can be at most 250 characters"
        assert err in str(e.value)


class TestRegion:
    def test_region_availability(self, mock_boto3):
        region = models.Region(commercial_regions=["us-east-1", "us-east-2"], future_region_support=True)
        assert region.future_region_support == True

    def test_region_availability_invalid_regions(self, mock_boto3):
        with pytest.raises(ValidationError):
            models.Region(commercial_regions=["us-east-1", "us-west-1"], future_region_support=True)

    def test_region_availability_future_region_supported(self, mock_boto3):
        region = models.Region(commercial_regions=["us-east-1", "us-east-2"], future_region_support=False)
        assert region.future_region_supported() == ["None"]


class TestAmiVersion:
    def _get_version_details(self):
        return {
            "version_title": "test_version_title",
            "release_notes": "test_release_notes\n",
            "ami_id": "ami-test",
            "access_role_arn": "arn:aws:iam::test",
            "os_user_name": "test_os_user_name",
            "os_system_version": "test_os_system_version",
            "os_system_name": "test_os",
            "scanning_port": 22,
            "usage_instructions": "test_usage_instructions",
            "recommended_instance_type": "m5.large",
            "ip_protocol": "tcp",
            "ip_ranges": ["0.0.0.0/0"],
            "from_port": 22,
            "to_port": 22,
        }

    def test_version_ami_id(self):
        model = models.AmiVersion(**self._get_version_details())
        assert model.ami_id == "ami-test"

    def test_version_ip_ranges(self):
        model = models.AmiVersion(**self._get_version_details())
        assert model.ip_ranges == ["0.0.0.0/0"]

    def test_invalid_access_role_arn(self):
        version = self._get_version_details()
        version["access_role_arn"] = "arn:iam::test"
        with pytest.raises(ValidationError):
            models.AmiVersion(**version)

    def test_invalid_ami_id(self):
        version = self._get_version_details()
        version["ami_id"] = "1234567"
        with pytest.raises(ValidationError):
            models.AmiVersion(**version)


class TestAmiProduct:
    @pytest.fixture
    def local_config(self):
        with open("./tests/test_config.yaml", "r") as f:
            return yaml.safe_load(f)

    def test_ami_product_short_description(self, mock_boto3, local_config):
        ami_product = models.AmiProduct(**local_config["product"])
        assert (
            ami_product.description.short_description == "test_short_description"
            and ami_product.description.product_title == "test"
        )

    def test_ami_product_region_availability(self, mock_boto3, local_config):
        ami_product = models.AmiProduct(**local_config["product"])
        assert ami_product.region.commercial_regions == ["us-east-1", "us-east-2"]

    def test_ami_product_version(self, mock_boto3, local_config):
        ami_product = models.AmiProduct(**local_config["product"])
        assert ami_product.version.ami_id == "ami-test"

    def test_ami_product_invalid_description(self, mock_boto3, local_config):
        local_config["product"]["description"]["categories"] = ["test"]
        with pytest.raises(ValidationError):
            ami_product = models.AmiProduct(**local_config["product"])

    def test_ami_product_invalid_region(self, mock_boto3, local_config):
        local_config["product"]["region"]["commercial_regions"].append("eu-west-3")
        with pytest.raises(ValidationError):
            ami_product = models.AmiProduct(**local_config["product"])


class TestInstanceTypePricing:
    @pytest.mark.parametrize(
        "instance_type_and_pricing,expected_hourly,expected_yearly",
        [
            ({"name": "c1.medium", "hourly": 0.004, "yearly": 24.528}, "0.004", "24.528"),
            ({"name": "c3.medium", "hourly": 0.012}, "0.012", "None"),
            ({"name": "c1.metal", "hourly": 0, "yearly": 0}, "0", "0"),
        ],
    )
    def test_instance_type_pricing(self, instance_type_and_pricing, expected_hourly, expected_yearly):
        model = models.InstanceTypePricing(**instance_type_and_pricing)
        assert str(model.price_hourly) == expected_hourly and str(model.price_annual) == expected_yearly

    def test_instance_type_without_hourly_pricing(self):
        instance_type_and_pricing: dict[str, Any] = {
            "name": "c1.medium",
        }

        with pytest.raises(ValidationError):
            models.InstanceTypePricing(**instance_type_and_pricing)

    def test_instance_type_with_four_digits(self):
        instance_type_and_pricing: dict[str, Any] = {
            "name": "c1.medium",
            "hourly": 0.0045,
            "yearly": 24.528,
        }

        with pytest.raises(ValidationError) as e:
            models.InstanceTypePricing(**instance_type_and_pricing)

        assert "must have at most 3 decimal places" in str(e.value)


class TestDescriptionModel:
    @pytest.mark.parametrize(
        "key, expected",
        [
            ("product_title", "test"),
            ("short_description", "test_short_description"),
            ("long_description", "test_long_description\n"),
            ("sku", "test"),
            ("highlights", ["test_highlight_1"]),
            ("search_keywords", ["test_keyword_1"]),
            ("categories", ["Migration"]),
        ],
    )
    def test_get_yaml_product_title(self, key, expected):
        data = {
            "ProductTitle": "test",
            "ProductCode": "prod-test",
            "ShortDescription": "test_short_description",
            "Manufacturer": "",
            "LongDescription": "test_long_description\n",
            "Sku": "test",
            "Highlights": ["test_highlight_1"],
            "AssociatedProducts": "",
            "SearchKeywords": ["test_keyword_1"],
            "Visibility": "Public",
            "ProductState": "Active",
            "Categories": ["Migration"],
        }
        description_model = models.DescriptionModel(**data)
        yaml_description = description_model._to_yaml()

        assert yaml_description.get(key) == expected


class TestPromotionalResourcesModel:
    @pytest.mark.parametrize(
        "key, expected",
        [
            ("logo_url", HttpUrl("https://test-logourl")),
            ("video_urls", [HttpUrl("https://test-video-url")]),
            ("additional_resources", [{"test-link": "https://test-url/"}]),
        ],
    )
    def test_get_yaml(self, key, expected):
        data = {
            "LogoUrl": "https://test-logourl",
            "Videos": ["https://test-video-url"],
            "AdditionalResources": [{"Type": "Link", "Text": "test-link", "Url": "https://test-url"}],
        }
        promotional_resources_model = models.PromotionalResourcesModel(**data)
        yaml_promotional_resources = promotional_resources_model._to_yaml()

        assert yaml_promotional_resources.get(key) == expected


class TestSupportInformationModel:
    @pytest.mark.parametrize(
        "key, expected",
        [
            ("support_description", "test_support_description"),
            ("support_resources", ["https://test_support_url"]),
        ],
    )
    def test_get_yaml(self, key, expected):
        data = {
            "Description": "test_support_description",
            "Resources": ["https://test_support_url"],
        }
        support_information_model = models.SupportInformationModel(**data)
        yaml_support_information = support_information_model._to_yaml()

        assert yaml_support_information.get(key) == expected


class TestRegionAvailabilityModel:
    @pytest.mark.parametrize(
        "key, expected",
        [
            ("commercial_regions", ["us-east-1", "us-east-2"]),
            ("future_region_support", True),
        ],
    )
    def test_get_yaml(self, key, expected):
        data = {
            "Regions": ["us-east-1", "us-east-2"],
            "FutureRegionSupport": "All",
        }
        support_information_model = models.RegionAvailabilityModel(**data)
        yaml_support_information = support_information_model._to_yaml()
        assert yaml_support_information.get(key) == expected

    @pytest.mark.parametrize(
        "key, expected",
        [
            ("commercial_regions", ["us-east-1", "us-east-2"]),
            ("future_region_support", False),
        ],
    )
    def test_get_yaml_future_region_false(self, key, expected):
        data = {
            "Regions": ["us-east-1", "us-east-2"],
            "FutureRegionSupport": "",
        }
        support_information_model = models.RegionAvailabilityModel(**data)
        yaml_support_information = support_information_model._to_yaml()
        assert yaml_support_information.get(key) == expected


class TestEntity:
    @pytest.fixture
    def get_entity(self):
        with open("./tests/test_config.json", "r") as f:
            response_json = json.load(f)

        with open("./tests/test_config.yaml", "r") as f:
            local_config = yaml.safe_load(f)

        # live_listing_response
        entity1 = models.EntityModel(**response_json)
        # local_config_response
        entity2 = models.EntityModel.get_entity_from_yaml(local_config)

        return entity1, entity2

    def test_valid_response(self):
        with open("./tests/test_config.json", "r") as f:
            response_json = json.load(f)

        entity_model = models.EntityModel(**response_json)
        assert entity_model.Description.ProductTitle == "test"

    def test_valid_response_pricing_term(self):
        with open("./tests/test_config.json", "r") as f:
            response_json = json.load(f)

        entity_model = models.EntityModel(**response_json)
        pricing = cast(models.PricingTermModel, entity_model.Terms[1])
        assert pricing.RateCards[0].RateCard[0].DimensionKey == "a1.large"

    def test_yaml_to_entity(self, mock_boto3):
        with open("./tests/test_config.yaml", "r") as f:
            local_config = yaml.safe_load(f)

        entity_model = models.EntityModel.get_entity_from_yaml(local_config)
        assert entity_model.Description.ProductTitle == "test"

    def test_yaml_to_entity_term(self, mock_boto3):
        with open("./tests/test_config.yaml", "r") as f:
            local_config = yaml.safe_load(f)

        entity_model = models.EntityModel.get_entity_from_yaml(local_config)
        refund_term = cast(models.SupportTermModel, entity_model.Terms[0])
        assert refund_term.RefundPolicy == "test_refund_policy_term\n"

    def test_non_valid_response(self):
        non_valid_response: dict[str, Any] = {
            "Description": {
                "ProductTitle": "test",
                "ProductCode": "prod-test",
            },
            "PromotionalResources": {
                "LogoUrl": "https://test-logourl",
            },
            "RegionAvailability": {"FutureRegionSupport": "All", "Restrict": [], "Regions": ["us-east-1", "us-east-2"]},
            "SupportInformation": {"Description": "test_support_description", "Resources": ["test_support_resource"]},
            "Versions": {"version1": ""},
        }
        with pytest.raises(ValidationError):
            models.EntityModel(**non_valid_response)

    @pytest.mark.parametrize(
        "name, value1, value2, expected",
        [
            ("test1", "", "test", models.DiffAddedModel(name="test1", value="test")),
            ("test2", "test", "", models.DiffRemovedModel(name="test2", value="test")),
            (
                "test3",
                "test",
                "testtest",
                models.DiffChangedModel(name="test3", old_value="test", new_value="testtest"),
            ),
        ],
    )
    def test_get_diff_model_type(self, name, value1, value2, expected):
        res = models.EntityModel.get_diff_model_type(name, value1, value2)
        assert res == expected

    @pytest.mark.parametrize(
        "custom_config, expected_diff",
        [
            (
                {},
                models.DiffModel(added=[], removed=[], changed=[]),
            ),
        ],
    )
    def test_get_changed_no_diff(self, mock_boto3, get_entity, custom_config, expected_diff):
        entity1, entity2 = get_entity

        for key, value in custom_config.items():
            setattr(entity2, key, value)
        assert entity1.get_diff(entity2) == expected_diff

    @pytest.mark.parametrize(
        "custom_config, expected_diff",
        [
            (
                {
                    "Description": models.DescriptionModel(
                        ProductTitle="test",
                        ShortDescription="test_short_description",
                        LongDescription="test",
                        Sku="test",
                        Highlights=["test_highlight_1"],
                        SearchKeywords=["test_keyword_1"],
                        Categories=["Migration"],
                    ),
                },
                models.DiffModel(
                    added=[],
                    removed=[],
                    changed=[
                        models.DiffChangedModel(
                            name="LongDescription", old_value="test_long_description", new_value="test"
                        )
                    ],
                ),
            ),
            (
                {
                    "Description": models.DescriptionModel(
                        ProductTitle="test",
                        ShortDescription="test_short_description",
                        LongDescription="test",
                        Sku="test",
                        Highlights=["test_highlight_1", "test_highlight_2"],
                        SearchKeywords=["test_keyword_1"],
                        Categories=["Migration"],
                    ),
                },
                models.DiffModel(
                    added=[],
                    removed=[],
                    changed=[
                        models.DiffChangedModel(
                            name="LongDescription", old_value="test_long_description", new_value="test"
                        ),
                        models.DiffChangedModel(
                            name="Highlights",
                            old_value=["test_highlight_1"],
                            new_value=["test_highlight_1", "test_highlight_2"],
                        ),
                    ],
                ),
            ),
        ],
    )
    def test_get_description_diff(self, mock_boto3, get_entity, custom_config, expected_diff):
        entity1, entity2 = get_entity
        for key, value in custom_config.items():
            setattr(entity2, key, value)
        assert entity1.get_diff(entity2) == expected_diff

    @pytest.mark.parametrize(
        "custom_config, expected_diff",
        [
            (
                {
                    "RegionAvailability": models.RegionAvailabilityModel(
                        Regions=["us-east-1"],
                        FutureRegionSupport="All",
                    ),
                },
                models.DiffModel(
                    added=[],
                    removed=[],
                    changed=[
                        models.DiffChangedModel(
                            name="Regions", old_value=["us-east-1", "us-east-2"], new_value=["us-east-1"]
                        )
                    ],
                ),
            ),
        ],
    )
    def test_get_region_diff(self, mock_boto3, get_entity, custom_config, expected_diff):
        entity1, entity2 = get_entity
        for key, value in custom_config.items():
            setattr(entity2, key, value)
        assert entity1.get_diff(entity2) == expected_diff

    @pytest.mark.parametrize(
        "custom_config, expected_diff",
        [
            (
                {
                    "PromotionalResources": models.PromotionalResourcesModel(
                        LogoUrl="https://test-logourl",
                        Videos=[],
                        AdditionalResources=[{"Text": "test-link1", "Url": "https://test-url/"}],
                    ),
                },
                models.DiffModel(
                    added=[],
                    removed=[],
                    changed=[
                        models.DiffChangedModel(
                            name="AdditionalResources",
                            old_value=[{"Text": "test-link", "Url": "https://test-url/"}],
                            new_value=[{"Text": "test-link1", "Url": "https://test-url/"}],
                        )
                    ],
                ),
            ),
            (
                {
                    "PromotionalResources": models.PromotionalResourcesModel(
                        LogoUrl="https://test-logourl",
                        Videos=["https://video-url"],
                        AdditionalResources=[{"Text": "test-link", "Url": "https://test-url/"}],
                    ),
                },
                models.DiffModel(
                    added=[models.DiffAddedModel(name="Videos", value=[HttpUrl("https://video-url")])],
                    removed=[],
                    changed=[],
                ),
            ),
        ],
    )
    def test_get_promotional_resource_diff(self, mock_boto3, get_entity, custom_config, expected_diff):
        entity1, entity2 = get_entity
        for key, value in custom_config.items():
            setattr(entity2, key, value)
        assert entity1.get_diff(entity2) == expected_diff

    @pytest.mark.parametrize(
        "custom_config, expected_diff",
        [
            (
                [{"Type": "SupportTerm", "RefundPolicy": "will be refunded"}],
                models.DiffModel(
                    added=[],
                    removed=[],
                    changed=[
                        models.DiffChangedModel(
                            name="SupportTerm",
                            old_value={"Type": "SupportTerm", "RefundPolicy": "test_refund_policy_term\n"},
                            new_value={"Type": "SupportTerm", "RefundPolicy": "will be refunded"},
                        )
                    ],
                ),
            ),
            (
                [{"Type": "SupportTerm", "RefundPolicy": ""}],
                models.DiffModel(
                    added=[],
                    removed=[],
                    changed=[
                        models.DiffChangedModel(
                            name="SupportTerm",
                            old_value={"Type": "SupportTerm", "RefundPolicy": "test_refund_policy_term\n"},
                            new_value={"Type": "SupportTerm", "RefundPolicy": ""},
                        )
                    ],
                ),
            ),
        ],
    )
    def test_get_term_refund_policy_diff(self, mock_boto3, get_entity, custom_config, expected_diff):
        entity1, entity2 = get_entity
        setattr(entity2, "Terms", custom_config)
        assert entity1.get_diff(entity2) == expected_diff

    @pytest.mark.parametrize(
        "index, custom_config, expected_diff",
        [
            (
                1,
                {
                    "Type": "UsageBasedPricingTerm",
                    "CurrencyCode": "USD",
                    "RateCards": [
                        {
                            "RateCard": [
                                {"DimensionKey": "a1.large", "Price": "0.004"},
                            ]
                        }
                    ],
                },
                models.DiffModel(
                    added=[],
                    removed=[
                        models.DiffRemovedModel(
                            name="UsageBasedPricingTerm", value={"DimensionKey": "a1.xlarge", "Price": "0.007"}
                        ),
                    ],
                    changed=[],
                ),
            ),
            (
                2,
                {
                    "Type": "ConfigurableUpfrontPricingTerm",
                    "CurrencyCode": "USD",
                    "RateCards": [
                        {
                            "Selector": {"Type": "Duration", "Value": "P365D"},
                            "Constraints": {
                                "MultipleDimensionSelection": "Allowed",
                                "QuantityConfiguration": "Allowed",
                            },
                            "RateCard": [
                                {"DimensionKey": "a1.large", "Price": "24.528"},
                                {"DimensionKey": "a1.xlarge", "Price": "80.0"},
                            ],
                        }
                    ],
                },
                models.DiffModel(
                    added=[],
                    removed=[],
                    changed=[
                        models.DiffChangedModel(
                            name="ConfigurableUpfrontPricingTerm",
                            old_value={"DimensionKey": "a1.xlarge", "Price": "49.056"},
                            new_value={"DimensionKey": "a1.xlarge", "Price": "80.0"},
                        ),
                    ],
                ),
            ),
            (
                2,
                {
                    "Type": "ConfigurableUpfrontPricingTerm",
                    "CurrencyCode": "USD",
                    "RateCards": [
                        {
                            "Selector": {"Type": "Duration", "Value": "P200D"},
                            "Constraints": {
                                "MultipleDimensionSelection": "Allowed",
                                "QuantityConfiguration": "Allowed",
                            },
                            "RateCard": [
                                {"DimensionKey": "a1.large", "Price": "24.528"},
                                {"DimensionKey": "a1.xlarge", "Price": "49.056"},
                            ],
                        }
                    ],
                },
                models.DiffModel(
                    added=[],
                    removed=[],
                    changed=[
                        models.DiffChangedModel(
                            name="ConfigurableUpfrontPricingTerm",
                            old_value={"Type": "Duration", "Value": "P365D"},
                            new_value={"Type": "Duration", "Value": "P200D"},
                        )
                    ],
                ),
            ),
            (
                2,
                {
                    "Type": "ConfigurableUpfrontPricingTerm",
                    "CurrencyCode": "USD",
                    "RateCards": [
                        {
                            "Selector": {"Type": "Duration", "Value": "P365D"},
                            "Constraints": {
                                "MultipleDimensionSelection": "Allowed",
                                "QuantityConfiguration": "Disallowed",
                            },
                            "RateCard": [
                                {"DimensionKey": "a1.large", "Price": "24.528"},
                                {"DimensionKey": "a1.xlarge", "Price": "49.056"},
                            ],
                        }
                    ],
                },
                models.DiffModel(
                    added=[],
                    removed=[],
                    changed=[
                        models.DiffChangedModel(
                            name="ConfigurableUpfrontPricingTerm",
                            old_value={"MultipleDimensionSelection": "Allowed", "QuantityConfiguration": "Allowed"},
                            new_value={"MultipleDimensionSelection": "Allowed", "QuantityConfiguration": "Disallowed"},
                        )
                    ],
                ),
            ),
        ],
    )
    def test_get_term_pricing_diff(self, mock_boto3, get_entity, index, custom_config, expected_diff):
        entity1, entity2 = get_entity
        entity2.Terms[index] = custom_config
        assert entity1.get_diff(entity2) == expected_diff

    def test_convert_terms_to_yaml_refund_policy(self, mock_boto3, get_entity):
        entity1, entity2 = get_entity

        res = entity1._convert_terms_to_yaml()
        assert res["refund_policy"] == "test_refund_policy_term\n"

    @pytest.mark.parametrize(
        "instance_type, hourly, yearly",
        [
            ("a1.large", "0.004", "24.528"),
            ("a1.xlarge", "0.007", "49.056"),
        ],
    )
    def test_convert_terms_to_yaml_instance_types(self, mock_boto3, get_entity, instance_type, hourly, yearly):
        entity1, entity2 = get_entity

        res = entity1._convert_terms_to_yaml()

        for pricing in res["instance_types"]:
            if pricing["name"] == instance_type:
                assert pricing["yearly"] == yearly and pricing["hourly"] == hourly

    @pytest.mark.parametrize(
        "key, expected",
        [
            ("product_title", "test"),
            ("short_description", "test_short_description"),
            ("search_keywords", ["test_keyword_1"]),
            ("logo_url", HttpUrl("https://test-logourl")),
            ("support_description", "test_support_description"),
            ("additional_resources", [{"test-link": "https://test-url/"}]),
        ],
    )
    def test_entity_to_yaml_description(self, key, expected):
        with open("./tests/test_config.json", "r") as f:
            response_json = json.load(f)

        entity_model = models.EntityModel(**response_json)
        yaml_config = entity_model._get_yaml_from_entity()
        assert yaml_config["product"]["description"][key] == expected

    @pytest.mark.parametrize(
        "key, expected",
        [("commercial_regions", ["us-east-1", "us-east-2"]), ("future_region_support", True)],
    )
    def test_entity_to_yaml_region(self, key, expected):
        with open("./tests/test_config.json", "r") as f:
            response_json = json.load(f)

        entity_model = models.EntityModel(**response_json)
        yaml_config = entity_model._get_yaml_from_entity()
        assert yaml_config["product"]["region"][key] == expected

    @pytest.mark.parametrize(
        "key, expected",
        [
            ("refund_policy", "test_refund_policy_term\n"),
            (
                "instance_types",
                [
                    {"name": "a1.large", "hourly": "0.004", "yearly": "24.528"},
                    {"name": "a1.xlarge", "hourly": "0.007", "yearly": "49.056"},
                ],
            ),
        ],
    )
    def test_entity_to_yaml_offer(self, key, expected):
        with open("./tests/test_config.json", "r") as f:
            response_json = json.load(f)

        entity_model = models.EntityModel(**response_json)
        yaml_config = entity_model._get_yaml_from_entity()
        assert yaml_config["offer"][key] == expected

    def test_entity_to_yaml_offer(self, key, expected):
        with open("./tests/test_config.json", "r") as f:
            response_json = json.load(f)

        entity_model = models.EntityModel(**response_json)
        yaml_config = entity_model._get_yaml_from_entity()
        assert yaml_config["offer"][key] == expected
