"""The evaluation tables (plan sections 8 and 9): raters, panels, ratings, findings, consensus, calibration sets.

The site never imports this app; this app may import `forum` and `moderation`. Human and LLM raters share every table.

Rules that matter are enforced twice, as in steps 4a and 4b: in `save()` (friendly `ValidationError`) and by database
constraints or triggers (`IntegrityError`) where SQLite can express them, so `bulk_create`, queryset updates and raw SQL
cannot bypass them. Relations are PROTECT: nothing cascades silently. The only references that are not foreign keys
are the ones the plan says must survive: a rating's `llm_call_id` (the spend ledger keeps plain integers) and the
`target_type` / `target_id` pair that points at a message or an intervention act.

Not enforced in the database (so that adding a dimension needs no schema change): that a dimension name is one of
`moderation.taxonomy.DIMENSIONS`; `save()` checks it.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.db.models.signals import m2m_changed
from django.dispatch import receiver

from moderation import taxonomy

from .targets import TARGET_TYPE_CHOICES, TARGET_TYPES, resolve_target, target_ref, target_text, validate_target

RATER_KINDS = ("human", "llm")
RATING_STATUSES = ("pending", "done", "failed")
LOW_INTENSITY, HIGH_INTENSITY = taxonomy.INTENSITY_RANGE


def _choices(values):
    return [(v, v) for v in values]


def _invalid(field, message):
    return ValidationError({field: message})


def _default_span_match_min_iou():
    return settings.SPAN_MATCH_MIN_IOU


def _default_intensity_disagreement_threshold():
    return settings.INTENSITY_DISAGREEMENT_THRESHOLD


def _check_dimension(dimension, field="dimension"):
    if dimension not in taxonomy.DIMENSIONS:
        raise _invalid(field, f"'{dimension}' is not a dimension; use one of {', '.join(taxonomy.DIMENSIONS)}.")


def _check_intensity(value, field="intensity"):
    if isinstance(value, bool) or not isinstance(value, int) or not LOW_INTENSITY <= value <= HIGH_INTENSITY:
        raise _invalid(field, f"The intensity must be a whole number from {LOW_INTENSITY} to {HIGH_INTENSITY}.")


def _check_span(text, start, end):
    ints = all(isinstance(v, int) and not isinstance(v, bool) for v in (start, end))
    if not ints or not 0 <= start < end <= len(text):
        raise _invalid("start", f"The span must satisfy 0 <= start < end <= {len(text)} (the length of the text).")


def _target_check(prefix):
    return models.CheckConstraint(condition=Q(target_type__in=list(TARGET_TYPES)), name=f"{prefix}_target_type_valid")


class TargetReference(models.Model):
    """Abstract base: a generic reference to a Message or an InterventionAct, validated to exist in `save()`."""

    target_type = models.CharField(max_length=20, choices=TARGET_TYPE_CHOICES)
    target_id = models.PositiveBigIntegerField()

    class Meta:
        abstract = True

    @property
    def target(self):
        """The Message or InterventionAct referred to (None if it does not exist)."""
        return resolve_target(self.target_type, self.target_id)

    @target.setter
    def target(self, obj):
        self.target_type, self.target_id = target_ref(obj)

    def target_text(self):
        return target_text(validate_target(self.target_type, self.target_id))


# ---------------------------------------------------------------------------------------------------------------------
# Raters, panels
# ---------------------------------------------------------------------------------------------------------------------


class Rater(models.Model):
    """A human (linked to a user) or an LLM (a provider, a model and a temperature) that rates targets."""

    KIND_CHOICES = _choices(RATER_KINDS)

    name = models.CharField(max_length=100, unique=True)
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    provider = models.CharField(max_length=50, blank=True, default="")
    model = models.CharField(max_length=100, blank=True, default="")
    temperature = models.FloatField(null=True, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="evaluation_raters"
    )
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(kind__in=list(RATER_KINDS)), name="evaluation_rater_kind_valid"),
            models.CheckConstraint(
                condition=(
                    Q(kind="human", user__isnull=False, provider="", model="", temperature__isnull=True)
                    | (Q(kind="llm", user__isnull=True) & ~Q(model=""))
                ),
                name="evaluation_rater_kind_rules",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.kind})"

    def validate_rules(self):
        if self.kind == "llm":
            if not (self.model or "").strip():
                raise _invalid("model", "An LLM rater needs a model.")
            if self.user_id is not None:
                raise _invalid("user", "An LLM rater has no user.")
        elif self.kind == "human":
            if self.user_id is None:
                raise _invalid("user", "A human rater needs a user.")
            if self.provider or self.model or self.temperature is not None:
                raise _invalid("model", "A human rater has no provider, model or temperature.")
        else:
            raise _invalid("kind", "The kind must be 'human' or 'llm'.")

    def save(self, *args, **kwargs):
        self.validate_rules()
        super().save(*args, **kwargs)


class Panel(models.Model):
    """A named, versioned set of raters plus the consensus rule they are merged under."""

    name = models.CharField(max_length=100)
    version = models.CharField(max_length=50, blank=True, default="")
    raters = models.ManyToManyField(Rater, through="PanelMember", related_name="panels", blank=True)
    # {dimension: {"rubric_version": "v1", "sha256": "..."}}: the rubric each dimension was rated against.
    dimensions = models.JSONField(default=dict, blank=True)
    span_match_min_iou = models.FloatField(default=_default_span_match_min_iou)
    intensity_disagreement_threshold = models.PositiveSmallIntegerField(
        default=_default_intensity_disagreement_threshold
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["name", "version"], name="evaluation_panel_name_version_unique"),
            models.CheckConstraint(
                condition=Q(span_match_min_iou__gt=0, span_match_min_iou__lte=1), name="evaluation_panel_iou_range"
            ),
            models.CheckConstraint(
                condition=Q(intensity_disagreement_threshold__gte=1), name="evaluation_panel_threshold_positive"
            ),
        ]

    def __str__(self):
        return f"{self.name} v{self.version}"

    def validate_rules(self):
        if not isinstance(self.dimensions, dict):
            raise _invalid("dimensions", "dimensions must map each dimension to its rubric version and hash.")
        for dimension in self.dimensions:
            _check_dimension(dimension, "dimensions")
        if not 0 < self.span_match_min_iou <= 1:
            raise _invalid("span_match_min_iou", "The span match threshold must be above 0 and at most 1.")
        if self.intensity_disagreement_threshold < 1:
            raise _invalid("intensity_disagreement_threshold", "The disagreement threshold must be at least 1.")

    def save(self, *args, **kwargs):
        self.validate_rules()
        super().save(*args, **kwargs)


class PanelMember(models.Model):
    """The through table of `Panel.raters` (explicit so that deleting a rater or a panel can never silently unlink)."""

    panel = models.ForeignKey(Panel, on_delete=models.PROTECT, related_name="memberships")
    rater = models.ForeignKey(Rater, on_delete=models.PROTECT, related_name="memberships")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["panel", "rater"], name="evaluation_panelmember_unique")]


# ---------------------------------------------------------------------------------------------------------------------
# Ratings and findings
# ---------------------------------------------------------------------------------------------------------------------


class Rating(TargetReference):
    """One rater's pass over one target (a message or an intervention act)."""

    STATUS_CHOICES = _choices(RATING_STATUSES)

    rater = models.ForeignKey(Rater, on_delete=models.PROTECT, related_name="ratings")
    # The dimensions this pass covered: a list of dimension names.
    dimensions = models.JSONField(default=list, blank=True)
    replicate = models.PositiveIntegerField(default=1)
    # A plain integer, not a foreign key: LLMCall rows are the spend ledger and must survive whatever happens here.
    llm_call_id = models.BigIntegerField(null=True, blank=True)
    guideline_version = models.CharField(max_length=100, blank=True, default="")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="pending")
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            _target_check("evaluation_rating"),
            models.CheckConstraint(condition=Q(replicate__gte=1), name="evaluation_rating_replicate_positive"),
            models.CheckConstraint(
                condition=Q(status__in=list(RATING_STATUSES)), name="evaluation_rating_status_valid"
            ),
        ]
        indexes = [models.Index(fields=["target_type", "target_id"], name="evaluation_rating_target_idx")]

    def __str__(self):
        return f"Rating {self.pk} by rater {self.rater_id} of {self.target_type} {self.target_id}"

    def validate_rules(self):
        validate_target(self.target_type, self.target_id)
        if not isinstance(self.dimensions, list):
            raise _invalid("dimensions", "dimensions must be a list of dimension names.")
        for dimension in self.dimensions:
            _check_dimension(dimension, "dimensions")
        if isinstance(self.replicate, bool) or not isinstance(self.replicate, int) or self.replicate < 1:
            raise _invalid("replicate", "The replicate number must be at least 1.")
        if self.status not in RATING_STATUSES:
            raise _invalid("status", f"The status must be one of {', '.join(RATING_STATUSES)}.")
        if self.llm_call_id is not None:
            if self.rater.kind != "llm":
                raise _invalid("llm_call_id", "Only an LLM rater's rating can have an LLM call.")
            from moderation.models import LLMCall

            if not LLMCall.objects.filter(pk=self.llm_call_id).exists():
                raise _invalid("llm_call_id", f"There is no LLM call with id {self.llm_call_id}.")
        if self.pk is not None:
            covered = set(self.dimensions)
            used = set(self.findings.values_list("dimension", flat=True))
            if used - covered:
                raise _invalid("dimensions", "A dimension that has findings cannot be removed from the rating.")
            old = Rating.objects.filter(pk=self.pk).values_list("target_type", "target_id").first()
            if old is not None and old != (self.target_type, self.target_id) and self.findings.exists():
                raise _invalid("target_id", "The target of a rating that has findings cannot change.")

    def save(self, *args, **kwargs):
        self.validate_rules()
        super().save(*args, **kwargs)


class Finding(models.Model):
    """One phrase, one dimension, one intensity (or not scorable, with a reason), inside a rating's target text."""

    NOT_SCORABLE_CHOICES = [("", "")] + _choices(taxonomy.NOT_SCORABLE_REASONS)

    rating = models.ForeignKey(Rating, on_delete=models.PROTECT, related_name="findings")
    local_id = models.CharField(max_length=50)
    dimension = models.CharField(max_length=50)
    start = models.PositiveIntegerField()
    end = models.PositiveIntegerField()
    quote = models.TextField()
    intensity = models.PositiveSmallIntegerField(null=True, blank=True)
    # Blank when the phrase has an intensity; one of the taxonomy's reasons when the intensity is null.
    not_scorable_reason = models.CharField(max_length=20, choices=NOT_SCORABLE_CHOICES, blank=True, default="")
    confidence = models.FloatField(null=True, blank=True)
    detail = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["rating", "local_id"], name="evaluation_finding_local_id_unique"),
            models.CheckConstraint(condition=Q(start__gte=0, end__gt=models.F("start")), name="evaluation_finding_span"),
            models.CheckConstraint(
                condition=Q(intensity__isnull=True) | Q(intensity__gte=LOW_INTENSITY, intensity__lte=HIGH_INTENSITY),
                name="evaluation_finding_intensity_range",
            ),
            models.CheckConstraint(
                condition=(
                    (Q(intensity__isnull=True) & Q(not_scorable_reason__in=list(taxonomy.NOT_SCORABLE_REASONS)))
                    | (Q(intensity__isnull=False) & Q(not_scorable_reason=""))
                ),
                name="evaluation_finding_not_scorable_rule",
            ),
        ]

    def __str__(self):
        return f"Finding {self.pk} {self.dimension} [{self.start}:{self.end}]"

    def validate_rules(self):
        _check_dimension(self.dimension)
        rating = self.rating
        if self.dimension not in rating.dimensions:
            raise _invalid("dimension", "The dimension is not among the dimensions the rating covers.")
        text = rating.target_text()
        _check_span(text, self.start, self.end)
        if text[self.start : self.end] != self.quote:
            raise _invalid("quote", "The quote must equal the target text at [start:end].")
        if self.intensity is None:
            if self.not_scorable_reason not in taxonomy.NOT_SCORABLE_REASONS:
                raise _invalid(
                    "not_scorable_reason",
                    f"A finding without an intensity needs a reason: {', '.join(taxonomy.NOT_SCORABLE_REASONS)}.",
                )
        else:
            _check_intensity(self.intensity)
            if self.not_scorable_reason:
                raise _invalid("not_scorable_reason", "A finding with an intensity has no not-scorable reason.")

    def save(self, *args, **kwargs):
        self.validate_rules()
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------------------------------------------------
# Consensus and the link to the Master's issues
# ---------------------------------------------------------------------------------------------------------------------


class ConsensusFinding(TargetReference):
    """Findings of several raters that mark the same phrase on one dimension, merged by `evaluation.consensus`."""

    panel = models.ForeignKey(Panel, on_delete=models.PROTECT, related_name="consensus_findings")
    dimension = models.CharField(max_length=50)
    start = models.PositiveIntegerField()
    end = models.PositiveIntegerField()
    n_raters = models.PositiveSmallIntegerField()
    intensity_mean = models.FloatField(null=True, blank=True)
    intensity_range = models.PositiveSmallIntegerField(null=True, blank=True)
    needs_adjudication = models.BooleanField(default=False)
    adjudicated_intensity = models.PositiveSmallIntegerField(null=True, blank=True)
    adjudicated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="adjudications"
    )
    findings = models.ManyToManyField(Finding, through="ConsensusFindingMember", related_name="consensus_findings", blank=True)

    class Meta:
        constraints = [
            _target_check("evaluation_consensusfinding"),
            models.CheckConstraint(
                condition=Q(start__gte=0, end__gt=models.F("start")), name="evaluation_consensusfinding_span"
            ),
            models.CheckConstraint(condition=Q(n_raters__gte=1), name="evaluation_consensusfinding_n_raters_positive"),
            models.CheckConstraint(
                condition=Q(intensity_mean__isnull=True)
                | Q(intensity_mean__gte=LOW_INTENSITY, intensity_mean__lte=HIGH_INTENSITY),
                name="evaluation_consensusfinding_mean_range",
            ),
            models.CheckConstraint(
                condition=Q(intensity_range__isnull=True)
                | Q(intensity_range__gte=0, intensity_range__lte=HIGH_INTENSITY - LOW_INTENSITY),
                name="evaluation_consensusfinding_range_range",
            ),
            models.CheckConstraint(
                condition=Q(adjudicated_intensity__isnull=True)
                | Q(adjudicated_intensity__gte=LOW_INTENSITY, adjudicated_intensity__lte=HIGH_INTENSITY),
                name="evaluation_consensusfinding_adjudicated_range",
            ),
        ]
        indexes = [models.Index(fields=["target_type", "target_id"], name="evaluation_cf_target_idx")]

    def __str__(self):
        return f"ConsensusFinding {self.pk} {self.dimension} [{self.start}:{self.end}]"

    def validate_rules(self):
        _check_dimension(self.dimension)
        text = self.target_text()
        _check_span(text, self.start, self.end)
        if self.n_raters is None or self.n_raters < 1:
            raise _invalid("n_raters", "A consensus finding merges the findings of at least one rater.")
        if self.intensity_mean is not None and not LOW_INTENSITY <= self.intensity_mean <= HIGH_INTENSITY:
            raise _invalid("intensity_mean", f"The mean must be from {LOW_INTENSITY} to {HIGH_INTENSITY}.")
        if self.intensity_range is not None and not 0 <= self.intensity_range <= HIGH_INTENSITY - LOW_INTENSITY:
            raise _invalid("intensity_range", "The range must be from 0 to the width of the intensity scale.")
        if self.adjudicated_intensity is not None:
            _check_intensity(self.adjudicated_intensity, "adjudicated_intensity")

    def save(self, *args, **kwargs):
        self.validate_rules()
        super().save(*args, **kwargs)


def check_member(consensus_finding, finding):
    """A merged finding must share the consensus finding's target and dimension. Raises ValidationError."""
    rating = finding.rating
    if finding.dimension != consensus_finding.dimension:
        raise ValidationError("A merged finding must have the same dimension as the consensus finding.")
    if (rating.target_type, rating.target_id) != (consensus_finding.target_type, consensus_finding.target_id):
        raise ValidationError("A merged finding must have the same target as the consensus finding.")


class ConsensusFindingMember(models.Model):
    """The through table of `ConsensusFinding.findings`."""

    consensus_finding = models.ForeignKey(ConsensusFinding, on_delete=models.PROTECT, related_name="memberships")
    finding = models.ForeignKey(Finding, on_delete=models.PROTECT, related_name="memberships")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["consensus_finding", "finding"], name="evaluation_consensusmember_unique")
        ]

    def save(self, *args, **kwargs):
        check_member(self.consensus_finding, self.finding)
        super().save(*args, **kwargs)


@receiver(m2m_changed, sender=ConsensusFindingMember)
def _validate_members(sender, instance, action, reverse, model, pk_set, **kwargs):
    """`consensus_finding.findings.add(...)` and `finding.consensus_findings.add(...)` obey the same-target rule."""
    if action != "pre_add" or not pk_set:
        return
    if reverse:
        for consensus_finding in ConsensusFinding.objects.filter(pk__in=pk_set):
            check_member(consensus_finding, instance)
    else:
        for finding in Finding.objects.filter(pk__in=pk_set).select_related("rating"):
            check_member(instance, finding)


class IssueFindingLink(models.Model):
    """Links the Master's detection (an issue) to the ground truth (a consensus finding), by span overlap."""

    issue = models.ForeignKey("moderation.Issue", on_delete=models.PROTECT, related_name="finding_links")
    consensus_finding = models.ForeignKey(ConsensusFinding, on_delete=models.PROTECT, related_name="issue_links")
    overlap = models.FloatField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["issue", "consensus_finding"], name="evaluation_issuefindinglink_unique"),
            models.CheckConstraint(condition=Q(overlap__gte=0, overlap__lte=1), name="evaluation_issuefindinglink_overlap"),
        ]

    def save(self, *args, **kwargs):
        if not 0 <= self.overlap <= 1:
            raise _invalid("overlap", "The overlap must be from 0 to 1.")
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------------------------------------------------
# Calibration sets and annotations
# ---------------------------------------------------------------------------------------------------------------------


class CalibrationSet(models.Model):
    """The sample of messages given to human raters, with the sampling seed and the strata drawn from."""

    name = models.CharField(max_length=100, unique=True)
    seed = models.BigIntegerField()
    # What was asked for and what was achieved, per dimension and stratum (see evaluation.calibration).
    strata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class CalibrationItem(TargetReference):
    """One target in a calibration set, with the stratum it was drawn for and its random presentation order."""

    set = models.ForeignKey(CalibrationSet, on_delete=models.PROTECT, related_name="items")
    stratum = models.CharField(max_length=100, blank=True, default="")
    order = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            _target_check("evaluation_calibrationitem"),
            models.UniqueConstraint(fields=["set", "target_type", "target_id"], name="evaluation_calibrationitem_unique"),
        ]
        ordering = ["order", "pk"]

    def __str__(self):
        return f"{self.target_type} {self.target_id} in set {self.set_id} (order {self.order})"

    def save(self, *args, **kwargs):
        validate_target(self.target_type, self.target_id)
        super().save(*args, **kwargs)


class Annotation(TargetReference):
    """A message-level label (`stance`, `tone`, `directional_effect`, ...) from the system itself or from a rater."""

    dimension = models.CharField(max_length=50)
    value = models.CharField(max_length=200)
    # "self" or "rater:<name>".
    source = models.CharField(max_length=110)
    rating = models.ForeignKey(Rating, null=True, blank=True, on_delete=models.PROTECT, related_name="annotations")
    confidence = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            _target_check("evaluation_annotation"),
            models.CheckConstraint(
                condition=Q(source="self") | (Q(source__startswith="rater:") & ~Q(source="rater:")),
                name="evaluation_annotation_source_valid",
            ),
        ]
        indexes = [models.Index(fields=["target_type", "target_id"], name="evaluation_annot_target_idx")]

    def __str__(self):
        return f"{self.dimension}={self.value} ({self.source})"

    def validate_rules(self):
        validate_target(self.target_type, self.target_id)
        if not (self.source == "self" or (self.source.startswith("rater:") and len(self.source) > len("rater:"))):
            raise _invalid("source", "The source must be 'self' or 'rater:<name>'.")

    def save(self, *args, **kwargs):
        self.validate_rules()
        super().save(*args, **kwargs)
