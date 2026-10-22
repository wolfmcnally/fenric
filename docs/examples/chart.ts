// Click or tap a bar to change its color. The chart fills the frame width
// and uses the frame height minus its top and bottom padding.
const months: { month: string; orders: number }[] = data;
const width = root.clientWidth;
const height = window.innerHeight - 20;
const margin = { top: 16, right: 16, bottom: 32, left: 48 };

const x = d3
  .scaleBand()
  .domain(months.map((d) => d.month))
  .range([margin.left, width - margin.right])
  .padding(0.2);
const y = d3
  .scaleLinear()
  .domain([0, d3.max(months, (d) => d.orders) ?? 0])
  .nice()
  .range([height - margin.bottom, margin.top]);

const svg = d3.select(root).append("svg").attr("viewBox", `0 0 ${width} ${height}`);
svg
  .append("g")
  .attr("fill", "steelblue")
  .selectAll("rect")
  .data(months)
  .join("rect")
  .attr("x", (d) => x(d.month) ?? 0)
  .attr("y", (d) => y(d.orders))
  .attr("width", x.bandwidth())
  .attr("height", (d) => y(0) - y(d.orders))
  .style("cursor", "pointer")
  .on("click", function () {
    d3.select(this).transition().duration(200).attr("fill", "orange");
  });
svg.append("g").attr("transform", `translate(0,${height - margin.bottom})`).call(d3.axisBottom(x));
svg.append("g").attr("transform", `translate(${margin.left},0)`).call(d3.axisLeft(y));
