// TYPE_ERROR: a string is not a number, so the type check refuses this chart.
const width: number = "wide";
d3.select(root).append("svg").attr("width", width);
