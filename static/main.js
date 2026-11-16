// NanoVNA Control Panel
// Bruce MacKinnon KC1FSZ

function setupTabNavigation(tabs) {
    $(tabs).focus(function(event) {
        $(this).find("[role='tablist']").find("[aria-selected='true']").focus();
    })
    $(tabs).find("[role='tab']").click(function(event) {
        var selectedEl = $(this).closest("[role='tablist']").find("[aria-selected='true']")
        // Unselect the current tab
        $(selectedEl).attr("aria-selected", "false")
        // Remove from tab rotation
        $(selectedEl).attr("tabindex", "-1")
        // Select the new tab
        $(this).attr("aria-selected", "true")
        // Add into tab rotation so that when someone back-tabs out of 
        // the tabpanel then end up on the selected tab control.
        $(this).attr("tabindex", "0")
        // Give focus
        $(this).focus()
        return false
    })
    $(tabs).find("[role='tab']").keydown(function(event) {
        if (event.which == 37 || event.which == 39) {
            var selectedEl = $(this).closest("[role='tablist']").find("[aria-selected='true']")
            // Unselect the current tab
            $(selectedEl).attr("aria-selected", "false")
            // Remove from tab rotation
            $(selectedEl).attr("tabindex", "-1")
            // Right arrow
            if (event.which == 39) {
                // Is there a next sibling? If so focus, if not wrap to the first
                if ($(selectedEl).next().length != 0) {
                    targetEl = $(selectedEl).next()
                } else {
                    targetEl = $(this).closest("[role='tablist']").children().first()
                }
            }
            // Left arrow 
            else {
                // Is there a previous sibling?  If so focus, if not wrap to the lset
                if ($(selectedEl).prev().length != 0) {
                    targetEl = $(selectedEl).prev()
                } else {
                    targetEl = $(this).closest("[role='tablist']").children().last()
                }
            }
            // Select the new tab
            $(targetEl).attr("aria-selected", "true")
            // Add into tab rotation so that when someone back-tabs out of 
            // the tabpanel then end up on the selected tab control.
            $(targetEl).attr("tabindex", "0")
            $(targetEl).focus()
            return false
        } else if (event.which == 9) {
            // Search up to find the parent tabs
            var tabsDiv = $(this).closest(".tabs")
            controlsId = $(this).attr("aria-controls")
            $(tabsDiv).find("#" + controlsId).focus()
            return false
        } else {
            return true;
        }
    });
    // This event handler is to control which tab panel is visible
    // when moving between tab controls.
    $(tabs).find("[role='tab']").focus(function(event) {
        controlsId = $(this).attr("aria-controls")
        // Search up
        var tabsDiv = $(this).closest(".tabs")
        // Hide all
        $(tabsDiv).find("[role=tabpanel]").hide()
        // Show the selection
        $(tabsDiv).find("#" + controlsId).show()
    });
}

// This function is used for implementing a multi-step workflow.  Each step
// has its own <div> that is shown in sequence.
function showStepDiv(divSet, step, prepareFunction) {
    // Hide all steps
    $(divSet).find(".step-div").hide()
    // Show the step that was selected
    $(divSet).find(".step-div").eq(step).show()
    // Focus the right control for this step
    $(divSet).find(".step-div").eq(step).find(".step-focus").focus()
    // Call prepare
    if (prepareFunction != null) {
        prepareFunction($(divSet).find(".step-div").eq(step), step)
    }
}

// Shows an error message in the panel's result area as plain text, so that
// any user input echoed back in the message is not interpreted as HTML
function showError(panel, message) {
    $(panel).find(".result-div").empty().append($("<p role='alert'></p>").text(message))
}

function do_sweep(panel) {

    // Get the form inputs
    var params = {
        cal_preset: $(panel).find(".s4").val(),
        start_frequency_mhz: $(panel).find(".s1").val(),
        end_frequency_mhz: $(panel).find(".s2").val(),
        step_frequency_mhz: $(panel).find(".s3").val(),
        vswr_thresholds: $(panel).find(".s5").val(),
        one_row: "true"
    }

    // Load up the sweep table user server-side data
    $.get("/api/sweep", params)
        .done(function(data) {
            if (data.error) {
                showError(panel, data.message)
            } else {
                $(panel).find(".result-div").html(template_0(data))
            }
        });
}

function do_complex_sweep(panel) {

    // Get the form inputs
    var params = {
        cal_preset: $(panel).find(".s4").val(),
        start_frequency_mhz: $(panel).find(".s1").val(),
        end_frequency_mhz: $(panel).find(".s2").val(),
        step_frequency_mhz: $(panel).find(".s3").val(),
        vswr_thresholds: $(panel).find(".s5").val(),
        one_row: "false"
    }

    // Load up the sweep table user server-side data
    $.get("/api/sweep", params)
        .done(function(data) {
            if (data.error) {
                showError(panel, data.message)
            } else {
                $(panel).find(".result-div").html(template_1(data))
            }
        });
}

/*
This function is used to validate the start/end frequency range that is
entered into the calibration form.  This makes sure that both values
have been entered and that the start is less than then end.

If a validation problem is detected then the workflow button is
marked to prevent progress to the next step.
*/
function validateCalibrationRange(panel) {

    startValue = $(panel).find(".s1").val().trim()
    endValue = $(panel).find(".s2").val().trim()

    try {
        if (startValue == "" || endValue == "") {
            throw "missing value"
        }
        var start = parseFloat(startValue)
        var end = parseFloat(endValue)
        if (isNaN(start) || isNaN(end) || end < start) {
            throw "range error"
        }
        // If we get to this point then the values are correct and we can
        // allow the workflow to proceed
        $(panel).find("#s30").text("Continue")
        $(panel).find("#s30").data("block-workflow", false)
    }
    catch (ex) {
        $(panel).find("#s30").text("Invalid Range")
        $(panel).find("#s30").data("block-workflow", true)
    }
}

// This function is called at the start of each calibration step to
// make any necessary adjustments to the step DIV.
function prepareCalStepDiv(stepDiv, stepNumber) {
    // On the first step we take a look at the status of the NanoVNA
    // and set the button text accordingly
    if (stepNumber == 0) {
        params = {
            step: 0
        }
        $.get("/api/calibrate", params)
        .done(function(data) {
            if (data == "OK") {
                $(stepDiv).find(".step-focus").text("Begin calibration")
            } else {
                $(stepDiv).find(".step-focus").text("NanoVNA is disconnected, please connect")
            }
        });
    }
}

// This function is called to perform a calibration step.  This is where
// we interact with the server/device.
function doCalStep(panel, step, successCb, errorCb) {

    var params;

    // STEP 0: Does nothing
    // STEP 3: Short
    // STEP 4: Open
    // STEP 5: Load
    // STEP 6: Repeat
    if (step == 0 || step == 3 || step == 4 || step == 5 || step == 6) {
        params = {
            step: step
        }
    }
    // STEP 1: Establishes preset
    else if (step == 1) {
        params = {
            step: step,
            cal_preset: $(panel).find(".s4").val()
        }
    }
    // STEP 2: Establishes range
    else if (step == 2) {
        params = {
            step: step,
            start_frequency_mhz: $(panel).find(".s1").val(),
            end_frequency_mhz: $(panel).find(".s2").val()
        }
    }

    $.get("/api/calibrate", params)
        .done(function(data) {
            if (data == "OK") {
                $(panel).find(".result-div").empty()
                successCb()
            } else {
                if (data.error) {
                    showError(panel, "Calibration failed: " + data.message)
                }
                errorCb()
            }
        });
}

function do_status_refresh(panel) {
    // Load up the sweep table user server-side data
    $.get("/api/status")
        .done(function(data) {
            if (data.error) {
                showError(panel, data.message)
            } else {
                $(panel).find(".result-div").html(template_2(data))
            }
        });
}

// Enables the custom velocity factor field only when the "Custom" cable
// type is selected
function updateVelocityFactorInput(panel) {
    var custom = $(panel).find(".cable-type").val() == "custom"
    $(panel).find(".vf-input").prop("disabled", !custom)
}

// Returns the velocity factor of the selected cable type, or the custom one
function selectedVelocityFactor(panel) {
    var velocityFactor = $(panel).find(".cable-type").val()
    if (velocityFactor == "custom") {
        velocityFactor = $(panel).find(".vf-input").val()
    }
    return velocityFactor
}

function do_tdr(panel) {

    // Get the form inputs
    var params = {
        cal_preset: $(panel).find(".s4").val(),
        max_length_m: $(panel).find(".max-length").val(),
        max_frequency_mhz: $(panel).find(".max-frequency").val(),
        velocity_factor: selectedVelocityFactor(panel)
    }

    $(panel).find(".result-div").html("<p role='status'>Measuring...</p>")
    $.get("/api/tdr", params)
        .done(function(data) {
            if (data.error) {
                showError(panel, data.message)
            } else {
                $(panel).find(".result-div").html(template_4(data))
            }
        })
        .fail(function() {
            showError(panel, "Unable to measure: no answer from the controller")
        });
}

function do_cable_length(panel) {

    // Get the form inputs
    var params = {
        cal_preset: $(panel).find(".s4").val(),
        start_frequency_mhz: $(panel).find(".s1").val(),
        end_frequency_mhz: $(panel).find(".s2").val(),
        velocity_factor: selectedVelocityFactor(panel)
    }

    $(panel).find(".result-div").html("<p role='status'>Measuring...</p>")
    $.get("/api/cable_length", params)
        .done(function(data) {
            if (data.error) {
                showError(panel, data.message)
            } else {
                $(panel).find(".result-div").html(template_3(data))
            }
        });
}

// Limits of the sweep points setting, replaced by the server's values
// (from /api/config) once the configuration is loaded
var sweepPointsLimits = { min: 11, max: 10001, default: 101 }

// Corrects a serial port entry: removes spaces and turns a bare number
// into a COM port name
function normalizePort(v) {
    v = v.replace(/\s/g, '')
    if (v != "" && !isNaN(v)) {
        v = "COM" + v
    }
    return v
}

// Corrects a sweep points entry: an invalid entry returns the fallback
// value, a number out of range is clamped
function normalizePoints(v, fallback) {
    var n = parseInt(String(v).trim(), 10)
    if (isNaN(n)) {
        n = parseInt(fallback, 10)
        if (isNaN(n)) {
            n = sweepPointsLimits.default
        }
    }
    return Math.min(sweepPointsLimits.max, Math.max(sweepPointsLimits.min, n))
}

// Fills the administration inputs with the saved configuration
function showConfig(panel, data) {
    if (data.points_limits) {
        sweepPointsLimits = data.points_limits
    }
    $(panel).find(".port-input").val(data.port)
    var points = data.points || sweepPointsLimits.default
    $(panel).find(".points-input")
        .attr("min", sweepPointsLimits.min)
        .attr("max", sweepPointsLimits.max)
        .val(points).data("applied", points)
}

// Saves the administration inputs on the server
function do_apply_config(panel) {
    // Pressing Enter applies without leaving the field, so correct all
    // fields first
    var portInput = $(panel).find(".port-input")
    var pointsInput = $(panel).find(".points-input")
    portInput.val(normalizePort(portInput.val()))
    pointsInput.val(normalizePoints(pointsInput.val(), pointsInput.data("applied")))

    var params = {
        port: portInput.val(),
        points: pointsInput.val()
    }
    $.post("/api/config", params)
        .done(function(data) {
            if (data.error) {
                showError(panel, data.message)
            } else {
                showConfig(panel, data)
                $(panel).find(".result-div").empty().append(
                    $("<p role='status'></p>").text("Settings saved."))
            }
        })
        .fail(function() {
            showError(panel, "Unable to save the settings")
        });
}
