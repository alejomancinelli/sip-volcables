"""Tests de la forma del resultado: completado del área y el centroide, traslado al
espacio del frame de cámara y serialización.
"""

import numpy as np

from system.inference.result import (
    ROI_STATE_NONE, ROI_STATE_NOK, ROI_STATES, Detection, InferenceResult, offset_detections,
    scale_detections,
)


def _detection(**overrides) -> Detection:
    fields = {"class_index": 0, "class_name": "grain", "confidence_pct": 90.0,
              "bbox_px": (10, 20, 30, 40)}
    fields.update(overrides)
    return Detection(**fields)


class TestDetection:
    def test_the_area_comes_from_the_bbox_when_it_is_not_given(self):
        assert _detection().area_px == 400

    def test_a_given_area_is_respected(self):
        """Con máscara el área es la de la forma, no la de la caja."""
        assert _detection(area_px=137).area_px == 137

    def test_the_centroid_comes_from_the_bbox_when_it_is_not_given(self):
        assert _detection().centroid_px == (20, 30)

    def test_a_given_centroid_is_respected(self):
        assert _detection(centroid_px=(11, 12)).centroid_px == (11, 12)

    def test_a_degenerate_bbox_has_no_area(self):
        assert _detection(bbox_px=(10, 10, 10, 10)).area_px == 0

    def test_the_mask_does_not_go_to_the_dict(self):
        detection = _detection(mask=np.ones((20, 20), np.uint8))
        assert detection.to_dict()["has_mask"] is True
        assert "mask" not in detection.to_dict()

    def test_the_dict_carries_plain_types(self):
        payload = _detection().to_dict()
        assert payload["bbox_px"] == [10, 20, 30, 40]
        assert payload["centroid_px"] == [20, 30]


class TestOffsetDetections:
    def test_it_moves_the_bbox_and_the_centroid(self):
        detection = _detection()
        offset_detections([detection], 100, 200)
        assert detection.bbox_px == (110, 220, 130, 240)
        assert detection.centroid_px == (120, 230)

    def test_the_area_does_not_change(self):
        detection = _detection()
        offset_detections([detection], 100, 200)
        assert detection.area_px == 400

    def test_a_zero_offset_changes_nothing(self):
        detection = _detection()
        offset_detections([detection], 0, 0)
        assert detection.bbox_px == (10, 20, 30, 40)


class TestScaleDetections:
    def test_it_scales_the_bbox_and_the_centroid(self):
        detection = _detection()
        scale_detections([detection], 0.5, 2.0)
        assert detection.bbox_px == (5, 40, 15, 80)
        assert detection.centroid_px == (10, 60)

    def test_the_area_of_a_bbox_follows_the_new_size(self):
        detection = _detection()
        scale_detections([detection], 0.5, 0.5)
        assert detection.area_px == 100

    def test_the_mask_is_resized_to_the_new_bbox(self):
        """La máscara va recortada al bbox: si el bbox cambia de tamaño, ella también."""
        detection = _detection(mask=np.ones((20, 20), np.uint8), area_px=400)
        scale_detections([detection], 0.5, 0.25)
        assert detection.mask.shape == (5, 10)

    def test_the_area_of_a_mask_is_counted_again(self):
        mask = np.zeros((20, 20), np.uint8)
        mask[:, :10] = 1
        detection = _detection(mask=mask, area_px=200)
        scale_detections([detection], 2.0, 2.0)
        assert detection.area_px == 800

    def test_the_mask_stays_binary(self):
        mask = np.zeros((20, 20), np.uint8)
        mask[5:15, 5:15] = 1
        detection = _detection(mask=mask)
        scale_detections([detection], 1.7, 0.6)
        assert set(np.unique(detection.mask)) <= {0, 1}

    def test_a_unit_scale_changes_nothing(self):
        detection = _detection()
        scale_detections([detection], 1.0, 1.0)
        assert (detection.bbox_px, detection.area_px) == ((10, 20, 30, 40), 400)


class TestInferenceResult:
    def test_the_count_follows_the_detections(self):
        result = InferenceResult(camera_slot="camera_1", detections=[_detection(), _detection()])
        assert result.detection_count == 2

    def test_a_fresh_result_is_valid(self):
        assert InferenceResult(camera_slot="camera_1").is_valid is True

    def test_the_dict_is_serializable_without_frames(self):
        import json
        result = InferenceResult(
            camera_slot="camera_1", pipeline_slot="pipeline_1",
            detections=[_detection()], metrics={"count": 1},
            source_bgr=np.zeros((4, 4, 3), np.uint8),
            annotated_bgr=np.zeros((4, 4, 3), np.uint8))
        payload = result.to_dict()
        assert "source_bgr" not in payload and "annotated_bgr" not in payload
        assert json.dumps(payload)

    def test_the_region_does_not_go_to_the_dict(self):
        result = InferenceResult(camera_slot="camera_1",
                                 region_bgr=np.zeros((4, 4, 3), np.uint8))
        assert "region_bgr" not in result.to_dict()

    def test_a_fresh_result_has_no_labels(self):
        assert InferenceResult(camera_slot="camera_1").labels == {}

    def test_the_labels_travel_in_the_dict(self):
        """El veredicto de frame llega al JSON del dataset y a la telemetría como texto."""
        result = InferenceResult(camera_slot="camera_1", labels={"belt": "full"})
        assert result.to_dict()["labels"] == {"belt": "full"}

    def test_the_labels_of_the_dict_are_a_copy(self):
        result = InferenceResult(camera_slot="camera_1", labels={"belt": "full"})
        result.to_dict()["labels"]["belt"] = "empty"
        assert result.labels == {"belt": "full"}

    def test_the_cycle_time_travels_in_the_dict(self):
        result = InferenceResult(camera_slot="camera_1", cycle_time_ms=412.84)
        assert result.to_dict()["cycle_time_ms"] == 412.8

    def test_the_confidence_key_is_the_one_the_save_conditions_read(self):
        """`image_collector.conditions` filtra por 'confidence_pct': el nombre es contrato."""
        assert "confidence_pct" in InferenceResult(camera_slot="camera_1").to_dict()


class TestRoiState:
    def test_a_fresh_result_says_nothing_about_its_roi(self):
        assert InferenceResult(camera_slot="camera_1").roi_state == ROI_STATE_NONE

    def test_the_state_travels_in_the_dict(self):
        """Lo guardado con el frame dice con qué estado se pintó el recuadro."""
        result = InferenceResult(camera_slot="camera_1", roi_state=ROI_STATE_NOK)
        assert result.to_dict()["roi_state"] == "nok"

    def test_the_vocabulary_has_no_repeats_and_none_is_empty(self):
        assert ROI_STATE_NONE == ""
        assert len(set(ROI_STATES)) == len(ROI_STATES) == 3
        assert ROI_STATE_NONE not in ROI_STATES
